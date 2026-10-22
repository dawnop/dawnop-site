"""Byte-progress watchdog and cgroup evidence for real unit transfers."""

import json
import socket
import threading
import time
from pathlib import Path


class Progress:
    def __init__(self, units, work, label, *, idle=10, total=180):
        self.units, self.work, self.label = units, work, label
        self.idle, self.total = idle, total
        self.started = self.advanced = time.monotonic()
        self.max_gap = 0
        self.counts = dict(sent=0, received=0, assembled=0, staged=0, upstream=0)
        self.peer_start = units.peer.upload_bytes
        self.phase = "ingress"
        self.ingress_done = self.first_response = None
        self.cgroups = {}
        self.curl_pids = set()
        self.errors = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.sample)
        self.thread.start()

    def advance(self, **values):
        with self.lock:
            now = time.monotonic()
            self.max_gap = max(self.max_gap, now - self.advanced)
            if any(value > self.counts[key] for key, value in values.items()):
                self.advanced = now
            for key, value in values.items():
                self.counts[key] = max(self.counts[key], value)

    def check(self):
        with self.lock:
            if self.errors:
                raise AssertionError("transfer sampler: " + repr(self.errors))
            now = time.monotonic()
            if now - self.started > self.total:
                raise AssertionError("transfer total deadline: " + self.label)
            if now - self.advanced > self.idle:
                raise AssertionError("transfer stalled: " + self.label)

    def sample(self):
        parent = None
        try:
            with (self.work / (self.label + ".jsonl")).open("w") as log:
                while not self.stop.wait(0.25):
                    if parent is None:
                        parent = next(
                            Path("/sys/fs/cgroup").rglob(self.units.name + ".slice"),
                            None,
                        )
                    groups = []
                    for cg in parent.glob("*.service") if parent else []:
                        try:
                            group = {"path": str(cg)}
                            for name in (
                                "memory.current",
                                "memory.events",
                                "memory.stat",
                                "memory.pressure",
                                "memory.max",
                                "io.stat",
                                "io.pressure",
                                "io.max",
                                "cpu.stat",
                                "cpu.max",
                            ):
                                group[name] = (
                                    (cg / name).read_text()
                                    if (cg / name).exists()
                                    else ""
                                )
                            group["processes"] = []
                            for pid in (cg / "cgroup.procs").read_text().split():
                                proc = Path("/proc") / pid
                                try:
                                    process = {
                                        "pid": pid,
                                        "comm": (proc / "comm").read_text().strip(),
                                        "io": (proc / "io").read_text(),
                                        "wchan": (proc / "wchan").read_text(),
                                        "files": {},
                                    }
                                    # Only owned curl staging files count as disk
                                    # progress. Repeated reads/CPU do not count.
                                    for name, counter in (
                                        ("upload", "assembled"),
                                        ("body", "staged"),
                                    ):
                                        for path in (proc / "root/tmp").glob(
                                            "site-curl-*/" + name
                                        ):
                                            size = path.stat().st_size
                                            process["files"][str(path)] = size
                                            self.advance(**{counter: size})
                                    if process["comm"] == "curl":
                                        env = (proc / "environ").read_bytes()
                                        maps = (proc / "maps").read_text()
                                        if (
                                            any(
                                                key in env
                                                for key in (
                                                    b"LD_LIBRARY_PATH=",
                                                    b"LD_PRELOAD=",
                                                    b"LD_AUDIT=",
                                                )
                                            )
                                            or str(self.units.release) in maps
                                        ):
                                            self.errors.append(
                                                "curl private loader leak"
                                            )
                                        self.curl_pids.add(pid)
                                    group["processes"].append(process)
                                except (FileNotFoundError, ProcessLookupError):
                                    pass
                            self.cgroups[str(cg)] = group
                            groups.append(group)
                        except FileNotFoundError:
                            pass
                    self.advance(
                        upstream=self.units.peer.upload_bytes - self.peer_start
                    )
                    with self.lock:
                        record = {
                            "s": round(time.monotonic() - self.started, 3),
                            "phase": self.phase,
                            **self.counts,
                            "cgroups": groups,
                        }
                    log.write(json.dumps(record) + "\n")
                    log.flush()
        except Exception as error:
            self.errors.append(repr(error))

    def exchange(self, client, data, body=None, output=None):
        # A short poll is not a response deadline. Only useful bytes extend the
        # no-progress budget, with an independent total bound on every exchange.
        client.settimeout(0.25)
        sent = received = 0

        def send(block):
            nonlocal sent
            view = memoryview(block)
            while view:
                self.check()
                try:
                    count = client.send(view)
                except TimeoutError:
                    continue
                if not count:
                    raise AssertionError("transfer send closed")
                view = view[count:]
                sent += count
                self.advance(sent=sent)

        send(data)
        if body:
            with body.open("rb") as stream:
                while block := stream.read(65536):
                    send(block)
        client.shutdown(socket.SHUT_WR)
        self.ingress_done = time.monotonic()
        self.phase = "response"
        wire = bytearray()
        stream = output.open("wb") if output else None
        try:
            while True:
                self.check()
                try:
                    block = client.recv(65536)
                except TimeoutError:
                    continue
                if not block:
                    break
                if self.first_response is None:
                    self.first_response = time.monotonic()
                received += len(block)
                self.advance(received=received)
                if stream:
                    stream.write(block)
                    if len(wire) < 100:
                        wire.extend(block[: 100 - len(wire)])
                else:
                    wire.extend(block)
        finally:
            if stream:
                stream.close()
        return bytes(wire)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise AssertionError("transfer sampler did not stop")
        self.advance(upstream=self.units.peer.upload_bytes - self.peer_start)
        result = {
            "label": self.label,
            **self.counts,
            "wall_s": round(time.monotonic() - self.started, 3),
            "max_gap_s": round(self.max_gap, 3),
            "response_wait_s": round(self.first_response - self.ingress_done, 3)
            if self.first_response
            else None,
            "idle_s": self.idle,
            "total_s": self.total,
            "cgroups": self.cgroups,
            "curl_pids": sorted(self.curl_pids),
            "errors": self.errors,
        }
        (self.work / (self.label + ".json")).write_text(
            json.dumps(result, indent=2) + "\n"
        )
        return result
