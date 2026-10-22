"""Exercise real local systemd units against disposable fixtures, never production."""

import argparse
import concurrent.futures
import importlib.util
import json
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
import contract_fixture  # noqa: E402
import contract_run  # noqa: E402


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


sys.path.insert(0, str(HERE.parent / "native-entry"))
entry = module("entry", HERE.parent / "native-entry/run.py")
package = module("native_package", HERE / "package.py")
deploy = module("native_install", ROOT / "backend-dawn/deploy/deploy_native.py")
Progress = module("unit_progress", HERE / "progress.py").Progress


def command(argv, *, good=True):
    result = subprocess.run(
        list(map(str, argv)), capture_output=True, text=True, timeout=60
    )
    if good and result.returncode:
        raise AssertionError(f"command failed: {argv}\n{result.stdout}{result.stderr}")
    if argv[0] == "systemd-analyze":
        requested = {Path(p).name for p in argv[2:]}
        for line in result.stderr.splitlines():
            if any(name in line for name in requested) and re.search(
                r"Unknown (key|lvalue)|not executable|Invalid", line
            ):
                raise AssertionError("invalid unit settings: " + line)
        # Preserve unrelated manager diagnostics, without treating their warnings
        # as errors in the candidate. Jammy runners currently warn about snapd.
        return (result.stdout + result.stderr).strip()
    return result.stdout.strip()


def demand(value, label):
    if not value:
        raise AssertionError(label)


def negative(name, body, label):
    try:
        body()
    except (AssertionError, ValueError) as error:
        demand(label in str(error), f"wrong negative failure {name}: {error}")
        print(f"PASS negative {name}: red by {label}", flush=True)
        return
    raise AssertionError("negative survived: " + name)


class Units:
    def __init__(self, release, work):
        self.name = f"dawnop-s12-{os.getpid()}"
        self.base = Path("/run") / self.name
        self.base.mkdir(mode=0o755)
        self.release = self.base / "release"
        shutil.copytree(release, self.release)
        (self.release / "dawnop-native").chmod(0o755)
        self.data = self.base / "data"
        self.data.mkdir(mode=0o777)
        self.db = self.data / "fixture.db"
        contract_fixture.build(str(self.db))
        account = pwd.getpwnam("nobody")
        self.uid, self.gid = account.pw_uid, account.pw_gid
        self.client_uid = int(os.environ.get("SUDO_UID", "1000"))
        self.client_gid = int(os.environ.get("SUDO_GID", "1000"))
        self.peer = entry.Peer(work)
        self.envfile = self.base / "fixture.env"
        contract_run.env_file(self.envfile, self.db, 1, self.peer.base)
        self.envfile.chmod(0o644)
        self.env = {
            "DAWNOP_ENV": str(self.envfile),
            "DAWN_DB_PATH": str(self.db),
            "DAWN_SIMPLE_EXT": str(self.release / "extensions/libsimple.so"),
        }
        prepared = subprocess.run(
            [str(self.release / "dawnop-native"), "--migrate"],
            env={**os.environ, **self.env},
            capture_output=True,
            timeout=30,
        )
        demand(prepared.returncode == 0, "prepare: " + repr(prepared.stderr))
        for path in self.data.iterdir():
            os.chown(path, self.uid, self.gid)
            path.chmod(0o600)
        os.chown(self.data, self.uid, self.gid)
        self.paths = []
        self.original = {}
        for path in (release / "deploy").iterdir():
            if path.suffix not in {".socket", ".service", ".slice", ".timer"}:
                continue
            name = path.name.replace("dawnop-native", self.name)
            text = path.read_text().replace("dawnop-native", self.name)
            text = text.replace("/opt/dawnop-dawn/native/current", str(self.release))
            text = text.replace("/opt/dawnop/backend/.env", str(self.envfile))
            text = text.replace("/opt/dawnop/data/dawnop.db", str(self.db))
            text = text.replace(
                "ReadWritePaths=/opt/dawnop/data", f"ReadWritePaths={self.data}"
            )
            text = text.replace("User=dawnop", f"User={self.uid}").replace(
                "Group=dawnop", f"Group={self.gid}"
            )
            text = text.replace(
                f"SocketUser={self.uid}", f"SocketUser={self.client_uid}"
            )
            text = text.replace(
                "SocketGroup=www-data", f"SocketGroup={self.client_gid}"
            )
            text = text.replace("dawnop-paste-cleanup", self.name + "-jvm-cleanup")
            # The release binary keeps its original name, unlike the test units.
            text = text.replace(
                str(self.release / self.name), str(self.release / "dawnop-native")
            )
            target = Path("/run/systemd/system") / name
            demand(not target.exists(), "unique test unit")
            target.write_text(text)
            self.original[name] = text
            self.paths.append(target)
        self.sock = Path("/run") / self.name / "http.sock"
        self.template = Path("/run/systemd/system") / (self.name + "@.service")
        self.socket_unit = Path("/run/systemd/system") / (self.name + ".socket")
        command(["systemctl", "daemon-reload"])
        verified = command(["systemd-analyze", "verify", *self.paths])
        (work / "systemd-verify.txt").write_text(verified + "\n")

    def instances(self):
        output = command(
            [
                "systemctl",
                "list-units",
                "--state=active,activating",
                "--plain",
                "--no-legend",
                self.name + "@*.service",
            ]
        )
        return [line.split()[0] for line in output.splitlines() if line.strip()]

    def quiesce(self):
        deadline = time.monotonic() + 10
        while self.instances():
            demand(time.monotonic() < deadline, "instances quiesce")
            threading.Event().wait(0.05)

    def start(self):
        command(["systemctl", "start", self.name + ".socket"])

    def rewrite(self, path, old=None, new=None):
        command(["systemctl", "stop", self.name + ".socket"])
        for unit in self.instances():
            command(["systemctl", "stop", unit])
        text = self.original[path.name]
        if old:
            demand(old in text, "unit mutation anchor")
            text = text.replace(old, new)
        path.write_text(text)
        command(["systemctl", "daemon-reload"])
        self.start()

    def connect(self):
        client = socket.socket(socket.AF_UNIX)
        client.settimeout(10)
        client.connect(str(self.sock))
        return client

    def exchange(self, data, body=None, output=None, progress=None):
        with self.connect() as client:
            if progress:
                return progress.exchange(client, data, body, output)
            client.sendall(data)
            if body:
                with body.open("rb") as stream:
                    while block := stream.read(65536):
                        client.sendall(block)
            client.shutdown(socket.SHUT_WR)
            if output:
                with output.open("wb") as stream:
                    while block := client.recv(65536):
                        stream.write(block)
                with output.open("rb") as stream:
                    return stream.read(100)
            return entry.receive(client)

    def health(self):
        wire = self.exchange(entry.request("GET", "/api/health"))
        demand(
            wire.startswith(b"HTTP/1.1 200 OK") and wire.endswith(entry.HEALTH),
            "stdout-is-socket",
        )

    def concurrent(self, count=64):
        self.quiesce()
        clients = [self.connect() for _ in range(count)]
        try:
            for client in clients:
                try:
                    client.sendall(b"GET /api/health HTTP/1.1\r\nHost: local\r\n")
                except OSError as error:
                    raise AssertionError(
                        "64-live-instances: connection refused"
                    ) from error
            deadline = time.monotonic() + 10
            while len(self.instances()) < count and time.monotonic() < deadline:
                threading.Event().wait(0.05)
            demand(len(self.instances()) == count, "64-live-instances")
            while True:
                mapped = 0
                for unit in self.instances():
                    pid = command(
                        ["systemctl", "show", "-p", "MainPID", "--value", unit]
                    )
                    if pid != "0":
                        maps = Path(f"/proc/{pid}/maps").read_text()
                        sqlite = {
                            line.split()[-1]
                            for line in maps.splitlines()
                            if "libsqlite3.so" in line
                        }
                        # Type=exec may be active while the constructor is still starting.
                        if sqlite:
                            demand(
                                sqlite == {str(self.release / "lib/libsqlite3.so.0")},
                                "actual-private-SQLite-maps: " + repr(sqlite),
                            )
                            mapped += 1
                if mapped == count:
                    break
                demand(time.monotonic() < deadline, "64-private-maps")
                threading.Event().wait(0.05)
            for client in clients:
                client.sendall(b"\r\n")
                client.shutdown(socket.SHUT_WR)
            with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
                responses = list(pool.map(entry.receive, clients))
            demand(
                all(
                    w.startswith(b"HTTP/1.1 200 OK") and w.endswith(entry.HEALTH)
                    for w in responses
                ),
                "64-health-responses",
            )
            print(
                f"PASS {count} real simultaneous systemd instances, private maps and HTTP replies",
                flush=True,
            )
        finally:
            for client in clients:
                client.close()
            for unit in self.instances():
                command(["systemctl", "stop", unit])

    def close(self, work):
        for unit in [
            self.name + ".socket",
            self.name + "-cleanup.timer",
            *self.instances(),
            self.name + ".slice",
        ]:
            command(["systemctl", "stop", unit], good=False)
        logs = command(
            ["journalctl", "--no-pager", "-o", "cat", "-u", self.name + "*"], good=False
        )
        (work / "journal.txt").write_text(logs)
        for path in self.paths:
            if path.suffix == ".timer":
                command(["systemctl", "disable", "--runtime", path.name], good=False)
            path.unlink(missing_ok=True)
        self.peer.close()
        shutil.rmtree(self.base)
        command(["systemctl", "daemon-reload"])
        command(["systemctl", "reset-failed", self.name + "*"], good=False)


def artifact_controls(release, work):
    copy = work / "corrupt"
    shutil.copytree(release, copy)
    expected = json.loads((copy / "manifest.json").read_text())["commit"]
    deploy.verifier(copy, expected)
    demand(
        not (copy / "tools/__pycache__").exists(),
        "deployed verifier does not mutate the release",
    )
    print(
        "PASS deployed verifier: complete clean artifact, no bytecode writes",
        flush=True,
    )
    library = copy / "extensions/libsimple.so"
    original = library.read_bytes()
    library.write_bytes(original + b"bad")
    negative(
        "wrong so digest", lambda: package.verify(copy), "artifact file digest mismatch"
    )
    shutil.rmtree(copy)
    shutil.copytree(release, copy)
    (copy / "lib/libsqlite3.so.0").unlink()
    result = subprocess.run(
        [str(copy / "dawnop-native"), "--engine-info"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    demand(
        result.returncode == 78
        and "private SQLite missing" in result.stderr
        and not result.stdout,
        "missing-private-library-rejects-before-fallback",
    )
    print(
        "PASS negative missing private SQLite: exit 78 before system fallback",
        flush=True,
    )
    shutil.rmtree(copy)
    fake_run = {
        "head_sha": "a" * 40,
        "head_branch": "main",
        "event": "push",
        "path": ".github/workflows/ci.yml",
        "status": "in_progress",
        "conclusion": None,
    }
    negative(
        "unfinished CI",
        lambda: deploy.successful_run(fake_run, "a" * 40),
        "entire main CI",
    )
    negative("missing CI job", lambda: deploy.successful_jobs([]), "full CI jobs")
    archive = work / "bad.zip"
    archive.write_bytes(b"unverified")
    negative(
        "ZIP digest",
        lambda: deploy.check_zip(archive, "sha256:" + "0" * 64),
        "ZIP digest mismatch",
    )


def unit_controls(units):
    original = units.template.read_text()
    units.template.write_text(
        original.replace("CollectMode=inactive-or-failed\n", "").replace(
            "[Service]\n", "[Service]\nCollectMode=inactive-or-failed\n"
        )
    )
    try:
        negative(
            "unknown template setting",
            lambda: command(["systemd-analyze", "verify", units.template]),
            "invalid unit settings",
        )
    finally:
        units.template.write_text(original)
    units.start()
    denied = subprocess.run(
        [
            "setpriv",
            f"--reuid={units.uid}",
            f"--regid={units.gid}",
            "--clear-groups",
            "python3",
            "-c",
            "import socket,sys;s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1])",
            str(units.sock),
        ],
        capture_output=True,
        timeout=10,
    )
    demand(
        denied.returncode != 0 and b"PermissionError" in denied.stderr,
        "socket-permission-negative",
    )
    print(
        "PASS negative socket permissions: actual EACCES for excluded uid", flush=True
    )
    units.rewrite(units.socket_unit, "MaxConnections=64", "MaxConnections=2")
    negative("MaxConnections 2", units.concurrent, "64-live-instances")
    units.rewrite(units.socket_unit)
    units.rewrite(units.template, "StandardOutput=socket", "StandardOutput=journal")
    negative("stdout journal", units.health, "stdout-is-socket")
    journal = command(
        ["journalctl", "--no-pager", "-o", "cat", "-u", units.name + "@*"]
    )
    demand("HTTP/1.1 200 OK" in journal, "HTTP actually reached journal")
    units.rewrite(units.template)
    units.rewrite(
        units.template, f"ReadWritePaths={units.data}", f"ReadWritePaths={units.db}"
    )
    try:
        denied = units.exchange(
            entry.request(
                "MKCOL", "/dav/wal-denied", [("Authorization", "Basic " + entry.AUTH)]
            )
        )
    except ConnectionResetError:
        denied = b""
    demand(not denied.startswith(b"HTTP/1.1 201"), "WAL-directory-negative")
    journal = command(
        ["journalctl", "--no-pager", "-o", "cat", "-u", units.name + "@*"]
    )
    demand(
        "sqlite.cant_open" in journal
        or "readonly" in journal.lower()
        or "read-only" in journal.lower(),
        "WAL directory journal evidence",
    )
    print(
        "PASS negative WAL directory: writable db alone cannot create WAL/SHM",
        flush=True,
    )
    units.rewrite(units.template)
    good = units.exchange(
        entry.request(
            "MKCOL", "/dav/wal-allowed", [("Authorization", "Basic " + entry.AUTH)]
        )
    )
    demand(good.startswith(b"HTTP/1.1 201"), "WAL-directory-positive")


def timeout_kill(units):
    units.rewrite(
        units.template, "DAWNOP_SOCKET_IDLE_SEC=60", "DAWNOP_SOCKET_IDLE_SEC=1"
    )
    start = time.monotonic()
    with units.connect() as client:
        client.sendall(b"GET /api/health HTTP/1.1\r\n")
        try:
            entry.receive(client)
        except ConnectionResetError:
            pass
    demand(0.8 < time.monotonic() - start < 5, "socket-idle-timeout")
    units.quiesce()
    units.rewrite(units.template, "RuntimeMaxSec=10800", "RuntimeMaxSec=2")
    with units.connect() as client:
        client.sendall(b"GET /api/health HTTP/1.1\r\n")
        start = time.monotonic()
        entry.receive(client)
        demand(1.5 < time.monotonic() - start < 6, "RuntimeMaxSec enforced")
    units.quiesce()
    journal = command(
        ["journalctl", "--no-pager", "-o", "cat", "-u", units.name + "@*"]
    )
    demand(
        "runtime time limit" in journal.lower()
        and "result 'timeout'" in journal.lower(),
        "RuntimeMaxSec journal evidence",
    )
    units.rewrite(units.template)
    with units.connect() as client:
        client.sendall(b"GET /api/health HTTP/1.1\r\n")
        deadline = time.monotonic() + 5
        while not units.instances():
            demand(time.monotonic() < deadline, "kill target starts")
            threading.Event().wait(0.05)
        target = units.instances()[0]
        command(["systemctl", "kill", "--signal=KILL", "--kill-who=all", target])
    units.quiesce()
    units.health()
    print(
        "PASS socket idle timeout, real RuntimeMaxSec, killed instance and next activation recovery",
        flush=True,
    )


def cleanup_timers(units):
    jvm_service = Path("/run/systemd/system") / (units.name + "-jvm-cleanup.service")
    jvm_timer = jvm_service.with_suffix(".timer")
    jvm_service.write_text("[Service]\nType=oneshot\nExecStart=/usr/bin/true\n")
    text = (ROOT / "backend-dawn/deploy/dawnop-paste-cleanup.timer").read_text()
    jvm_timer.write_text(
        text.replace("dawnop-paste-cleanup", units.name + "-jvm-cleanup")
    )
    units.paths.extend([jvm_service, jvm_timer])
    command(["systemctl", "daemon-reload"])
    native = units.name + "-cleanup.timer"
    command(["systemctl", "start", jvm_timer.name])
    command(["systemctl", "start", native])
    demand(
        command(["systemctl", "is-active", jvm_timer.name], good=False) == "inactive",
        "native timer excludes JVM",
    )
    demand(
        command(["systemctl", "is-active", native]) == "active", "native timer active"
    )
    command(["systemctl", "start", jvm_timer.name])
    demand(
        command(["systemctl", "is-active", native], good=False) == "inactive",
        "JVM timer excludes native",
    )
    command(["systemctl", "stop", jvm_timer.name])
    print(
        "PASS real timer conflict in both directions; same binary native cleanup already ran",
        flush=True,
    )


def progress_negative(units, work):
    # Freeze a real PUT after activation. The client can initially fill its
    # socket buffer; that finite burst must not hide the stopped consumer.
    units.quiesce()
    progress = Progress(units, work, "stopped-upload", idle=2, total=15)
    target = None
    try:
        with units.connect() as client:
            client.sendall(b"PUT /dav/stopped HTTP/1.1\r\nHost: local\r\n")
            deadline = time.monotonic() + 5
            while not units.instances():
                demand(time.monotonic() < deadline, "progress negative activation")
                threading.Event().wait(0.05)
            target = units.instances()[0]
            command(["systemctl", "kill", "--kill-who=all", "--signal=STOP", target])
            negative(
                "stopped-upload-progress",
                lambda: progress.exchange(
                    client, b"Content-Length: 1048576\r\n\r\n" + b"x" * 1048576
                ),
                "transfer stalled",
            )
    finally:
        if target:
            command(
                ["systemctl", "kill", "--kill-who=all", "--signal=CONT", target],
                good=False,
            )
            command(["systemctl", "stop", target], good=False)
        progress.close()
    units.quiesce()
    units.health()


def transfers(units, work, label="shipped"):
    import hashlib

    payload = work / "payload.bin"
    digest = hashlib.sha256()
    with payload.open("wb") as stream:
        for _ in range(512 * 16):
            stream.write(entry.BLOCK)
            digest.update(entry.BLOCK)
    units.peer.payload = payload
    results = {}
    remote = f"/dav/offline-{label}-512.bin"
    try:
        progress = Progress(units, work, label + "-upload")
        try:
            upload = units.exchange(
                entry.request(
                    "PUT",
                    remote,
                    [
                        ("Authorization", "Basic " + entry.AUTH),
                        ("Content-Length", str(payload.stat().st_size)),
                    ],
                ),
                payload,
                progress=progress,
            )
        finally:
            results["upload"] = progress.close()
        demand(
            upload.startswith(b"HTTP/1.1 201"),
            "512 MiB upload status: " + repr(upload[:200]),
        )
        demand(
            units.peer.last_upload[1:] == (512 * 1024**2, digest.hexdigest()),
            "512 MiB upload full digest",
        )
        download = work / "download.wire"
        progress = Progress(units, work, label + "-download")
        try:
            units.exchange(
                entry.request(
                    "GET",
                    remote,
                    [("Authorization", "Basic " + entry.AUTH)],
                ),
                output=download,
                progress=progress,
            )
        finally:
            results["download"] = progress.close()
        _, size, actual = entry.download_digest(download)
        demand(
            size == 512 * 1024**2 and actual == digest.hexdigest(),
            "512 MiB download full digest",
        )
        download.unlink()
        for result in results.values():
            demand(not result["errors"], "transfer sampler errors")
            demand(result["cgroups"], "real transfer cgroup evidence")
            demand(
                result["max_gap_s"] < result["idle_s"], "transfer steady byte progress"
            )
            for group in result["cgroups"].values():
                events = dict(
                    line.split() for line in group["memory.events"].splitlines()
                )
                demand(events["oom"] == events["oom_kill"] == "0", "transfer no OOM")
                demand(
                    group["memory.max"].strip() == str(256 * 1024**2)
                    and group["memory.high"].strip() == str(192 * 1024**2),
                    "shipped MemoryHigh=192M MemoryMax=256M",
                )
        demand(
            all(r["curl_pids"] for r in results.values()),
            "curl system loader isolation sampled",
        )
        if label == "constrained":
            demand(
                results["upload"]["response_wait_s"] > 10,
                "reproduces old response timeout",
            )
            demand(
                results["upload"]["assembled"] > 500 * 1024**2,
                "multipart assembly progress observed",
            )
            demand(
                results["upload"]["upstream"] >= size,
                "upstream upload progress observed",
            )
            for result in results.values():
                for group in result["cgroups"].values():
                    quota, period = map(int, group["cpu.max"].split())
                    demand(quota / period == 0.25, "constrained CPUQuota applied")
                    demand(
                        "rbps=32000000 wbps=32000000" in group["io.max"],
                        "constrained IO limits applied",
                    )
                    counters = {"rbytes": 0, "wbytes": 0}
                    for line in group["io.stat"].splitlines():
                        fields = dict(item.split("=") for item in line.split()[1:])
                        for key in counters:
                            counters[key] += int(fields.get(key, 0))
                    demand(
                        min(counters.values()) >= size // 2,
                        "real constrained device IO observed",
                    )
                    full = next(
                        line
                        for line in group["io.pressure"].splitlines()
                        if line.startswith("full ")
                    )
                    demand(
                        int(full.split("total=")[1]) > 0,
                        "constrained IO pressure observed",
                    )
        print(
            f"PASS {label} real units 512 MiB upload/download full digest, clean curl loader; "
            f"response_wait_s={results['upload']['response_wait_s']} "
            f"max_gap_s={max(r['max_gap_s'] for r in results.values())}",
            flush=True,
        )
        return {"bytes": size, "sha256": actual, "progress": results}
    finally:
        payload.unlink()


def constrained_transfers(units, work):
    # v249 resolves the backing block device for an ordinary directory. Fail
    # if io.max/actual IO evidence cannot prove that the restriction took effect.
    text = units.original[units.template.name]
    constrained = text.replace("CPUQuota=100%", "CPUQuota=25%") + (
        "\nIOReadBandwidthMax=/tmp 32M\nIOWriteBandwidthMax=/tmp 32M\n"
    )
    demand(constrained != text, "constrained CPU anchor")
    units.rewrite(units.template, text, constrained)
    try:
        return transfers(units, work, "constrained")
    finally:
        units.rewrite(units.template)


def rollback(units, work):
    # The JVM has a real service too, with the same disposable database.
    units.quiesce()
    wire = units.exchange(
        entry.request(
            "MKCOL",
            "/dav/native-before-rollback",
            [("Authorization", "Basic " + entry.AUTH)],
        )
    )
    demand(wire.startswith(b"HTTP/1.1 201"), "native rollback write")
    units.quiesce()
    jvm = units.base / "jvm"
    jvm.mkdir()
    shutil.copy2(ROOT / "backend-dawn/backend-dawn.jar", jvm)
    shutil.copytree(ROOT / "backend-dawn/lib", jvm / "lib")
    (jvm / "extensions").mkdir()
    shutil.copy2(
        units.release / "extensions/libsimple.so", jvm / "extensions/libsimple.so"
    )
    probe_socket = socket.socket()
    probe_socket.bind(("127.0.0.1", 0))
    port = probe_socket.getsockname()[1]
    probe_socket.close()
    java = shutil.which("java")
    # ProtectHome hides the local JDK; copy a disposable JRE outside home.
    java_home = Path(java).resolve().parents[1]
    shutil.copytree(java_home, units.base / "jdk")
    java = units.base / "jdk/bin/java"
    unit = Path("/run/systemd/system") / (units.name + "-jvm.service")
    text = (ROOT / "backend-dawn/deploy/dawnop-dawn.service").read_text()
    text = text.replace("User=dawnop", f"User={units.uid}").replace(
        "Group=dawnop", f"Group={units.gid}"
    )
    text = text.replace("/opt/dawnop-dawn", str(jvm)).replace(
        "/opt/dawnop/backend/.env", str(units.envfile)
    )
    text = text.replace("/opt/dawnop/data/dawnop.db", str(units.db)).replace(
        "/opt/dawnop/data", str(units.data)
    )
    text = text.replace(
        "/opt/dawnop/backend/extensions/libsimple",
        str(jvm / "extensions/libsimple.so"),
    )
    text = text.replace("/usr/bin/java", str(java)).replace(
        "DAWN_PORT=8001", f"DAWN_PORT={port}"
    )
    unit.write_text(text)
    units.paths.append(unit)
    command(["systemctl", "daemon-reload"])
    try:
        command(["systemctl", "start", unit.name])
        command(["systemctl", "stop", units.name + ".socket"])
        deadline = time.monotonic() + 20
        import urllib.request

        while True:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=1
                ) as response:
                    demand(response.status == 200, "JVM rollback health")
                break
            except OSError:
                demand(time.monotonic() < deadline, "JVM rollback startup")
                threading.Event().wait(0.05)
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/dav/",
            method="PROPFIND",
            headers={"Authorization": "Basic " + entry.AUTH, "Depth": "1"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            demand(
                b"native-before-rollback" in response.read(),
                "JVM reads native committed data",
            )
        shutil.rmtree(units.release)
        with urllib.request.urlopen(request, timeout=5) as response:
            demand(
                b"native-before-rollback" in response.read(),
                "JVM reads same DB after native uninstall",
            )
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health", timeout=2
        ) as response:
            demand(response.status == 200, "JVM remains after native uninstall")
        print(
            "PASS rollback: native socket stopped and package removed; real JVM service reads native commit from same DB and stays healthy",
            flush=True,
        )
    finally:
        command(["systemctl", "stop", unit.name], good=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    args = parser.parse_args()
    demand(os.geteuid() == 0, "offline unit drill requires local root")
    args.work.mkdir(parents=True)
    started = time.monotonic()
    artifact_controls(args.release, args.work)
    units = Units(args.release, args.work)
    try:
        unit_controls(units)
        units.concurrent()
        timeout_kill(units)
        progress_negative(units, args.work)
        result = transfers(units, args.work)
        result["constrained"] = constrained_transfers(units, args.work)
        units.quiesce()
        command(["systemctl", "start", units.name + "-cleanup.service"])
        print("PASS same native binary cleanup oneshot", flush=True)
        cleanup_timers(units)
        rollback(units, args.work)
    finally:
        units.close(args.work)
    result["systemd"] = command(["systemctl", "--version"]).splitlines()[0]
    result["wall_s"] = round(time.monotonic() - started, 3)
    (args.work / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"PASS offline real systemd acceptance wall_s={result['wall_s']}", flush=True)


if __name__ == "__main__":
    main()
