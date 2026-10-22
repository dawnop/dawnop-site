"""Accept only: the production ELF owns every byte of each HTTP exchange."""

import json
import os
import re
import shutil
import signal
import socketserver
import subprocess
import threading
from pathlib import Path


def audit_execs(directory):
    attempts = []
    for path in sorted(directory.glob("exec-*.log")):
        for line in path.read_text(errors="replace").splitlines():
            if "execve(" not in line:
                continue
            attempts.append(line)
            # Inspect attempts, including failed execs, and arguments. Absolute
            # Java paths cannot evade the private PATH or this independent audit.
            if re.search(
                r'"[^"\n]*/(?:java|javaw|jshell)"|"-jar"|"[^"\n]*\.jar"', line
            ):
                raise AssertionError("native-no-jvm: " + line)
    if not attempts:
        raise AssertionError("native-no-jvm: missing exec trace")
    return len(attempts)


class NativeServer:
    def __init__(self, binary, env, port, work):
        self.binary = Path(binary).resolve()
        if self.binary.read_bytes()[:4] != b"\x7fELF":
            raise AssertionError("native launcher requires an ELF executable")
        self.work = Path(work)
        self.work.mkdir(parents=True)
        self.strace = shutil.which("strace")
        if not self.strace:
            raise AssertionError("native-no-jvm: strace is required")
        private = self.work / "path"
        private.mkdir()
        curl = shutil.which("curl")
        if not curl:
            raise AssertionError("curl is required")
        (private / "curl").symlink_to(curl)
        self.env = {**env, "PATH": str(private), "JAVA_HOME": ""}
        self.lock = threading.Lock()
        self.idle = threading.Condition()
        self.active = 0
        self.sequence = 0
        self.errors = []
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                with owner.idle:
                    owner.active += 1
                try:
                    owner.execute(stdin=self.request, stdout=self.request)
                except BaseException as error:
                    with owner.lock:
                        owner.errors.append(str(error))
                finally:
                    with owner.idle:
                        owner.active -= 1
                        owner.idle.notify_all()

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = False

        self.server = Server(("127.0.0.1", port), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)

    def execute(self, *args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE):
        with self.lock:
            index = self.sequence
            self.sequence += 1
        trace = self.work / f"exec-{index}.log"
        with (self.work / f"stderr-{index}.log").open("wb") as log:
            child = subprocess.Popen(
                [
                    self.strace,
                    "-f",
                    "-s",
                    "4096",
                    "-e",
                    "trace=execve",
                    "-o",
                    str(trace),
                    str(self.binary),
                    *args,
                ],
                stdin=stdin,
                stdout=stdout,
                stderr=log,
                env=self.env,
                start_new_session=True,
            )
            try:
                output, _ = child.communicate(timeout=45)
            except BaseException:
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate()
                raise
            result = subprocess.CompletedProcess(child.args, child.returncode, output)
        if result.returncode:
            raise AssertionError(
                f"native exchange {index}: exit {result.returncode}; see {log.name}"
            )
        return result

    def start(self):
        self.thread.start()

    def quiesce(self):
        # Reading the complete response can precede the process's bounded drain
        # and SQLite close. A fixture reset must wait for that actual owner.
        with self.idle:
            if not self.idle.wait_for(lambda: self.active == 0, timeout=45):
                raise AssertionError(
                    "native fixture reset while exchange still owns DB"
                )
        if self.errors:
            raise AssertionError("native exchanges failed: " + json.dumps(self.errors))

    def close(self):
        if self.thread.is_alive():
            self.server.shutdown()
            self.thread.join()
        self.server.server_close()  # joins every exchange before auditing
        count = audit_execs(self.work)
        if self.errors:
            raise AssertionError("native exchanges failed: " + json.dumps(self.errors))
        return {
            "processes": self.sequence,
            "exec_attempts": count,
            "jvm": 0,
            "path": "curl only",
        }
