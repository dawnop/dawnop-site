"""Exercise the actual single exchange entry over pipes and accepted sockets."""

import argparse
import base64
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BACKEND = ROOT / "backend-dawn"
sys.path.insert(0, str(HERE.parent))
import check_core  # noqa: E402
import contract_fixture  # noqa: E402
import contract_run  # noqa: E402
from fake import Peer  # noqa: E402
from mutant_workdir import require_workdir  # noqa: E402

BLOCK = bytes(range(256)) * 256
HEALTH = b'{"status":"ok","app":"dawnop-site"}'
AUTH = base64.b64encode(
    f"{contract_fixture.FIXTURE_USER}:{contract_fixture.FIXTURE_PW}".encode()
).decode()


def check(condition, label, detail=""):
    if not condition:
        raise AssertionError(f"{label}: {detail}")


def passed(label):
    print("PASS  " + label, flush=True)


def command(argv, timeout=300):
    result = subprocess.run(list(map(str, argv)), capture_output=True, timeout=timeout)
    check(result.returncode == 0, "command", result.stderr.decode(errors="replace"))
    return result


def request(method, path, headers=(), body=b""):
    return (
        f"{method} {path} HTTP/1.1\r\nHost: local\r\n".encode()
        + b"".join(f"{key}: {value}\r\n".encode() for key, value in headers)
        + b"\r\n"
        + body
    )


def expected(status, body, *, vary=True, headers=(), head=False, content_type=None):
    reasons = {
        200: "OK",
        400: "Bad Request",
        401: "Unauthorized",
        404: "Not Found",
        405: "Method Not Allowed",
        413: "Request Entity Too Large",
    }
    ct = content_type or "application/json; charset=utf-8"
    extra = list(headers) + ([("Vary", "Origin")] if vary else [])
    return (
        f"HTTP/1.1 {status} {reasons[status]}\r\nContent-Type: {ct}\r\n".encode()
        + b"".join(f"{key}: {value}\r\n".encode() for key, value in extra)
        + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + (b"" if head else body)
    )


def error_body(message):
    return json.dumps(
        {"detail": message}, ensure_ascii=False, separators=(",", ":")
    ).encode()


def pipe(binary, env, data=b"", argv=()):
    start = time.monotonic_ns()
    result = subprocess.run(
        [str(binary), *argv], input=data, capture_output=True, env=env, timeout=20
    )
    elapsed = (time.monotonic_ns() - start) / 1_000_000
    return result, elapsed


def exact(binary, env, data, wire, label):
    result, _ = pipe(binary, env, data)
    check(result.returncode == 0, label, result.stderr)
    check(result.stdout == wire, label, repr(result.stdout))
    check(not result.stderr.startswith(b"HTTP/"), "stderr-is-log")
    return result


def pair():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.create_connection(listener.getsockname(), timeout=3)
    accepted, _ = listener.accept()
    listener.close()
    return client, accepted


def receive(client):
    chunks = []
    while True:
        chunk = client.recv(65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def early(binary, env, *, pending=False, late=False):
    client, accepted = pair()
    child = None
    expected_wire = expected(
        401,
        error_body("需要认证"),
        vary=False,
        headers=[("WWW-Authenticate", 'Basic realm="dawnop-webdav"')],
    )
    data = request("PUT", "/dav/untrusted.bin", [("Content-Length", "65536")])
    try:
        # Queue the unread bytes before exec, making the no-drain reset deterministic.
        client.sendall(data + (b"" if pending or late else BLOCK))
        if not pending and not late:
            client.shutdown(socket.SHUT_WR)
        child = subprocess.Popen(
            [str(binary)],
            stdin=accepted,
            stdout=accepted,
            stderr=subprocess.PIPE,
            env=env,
        )
        accepted.close()
        client.settimeout(0.7)
        try:
            wire = receive(client)
        except (OSError, TimeoutError) as error:
            raise AssertionError(f"guard-cancel-propagates: {error}") from error
        check(wire == expected_wire, "guard-cancel-propagates", repr(wire))
        if late:
            client.sendall(BLOCK)
            client.shutdown(socket.SHUT_WR)
        if not pending:
            client.close()
        _, log = child.communicate(timeout=2)
        check(child.returncode == 0, "early-close-exit", log)
        check(not list(Path(env["TMPDIR"]).iterdir()), "early-reject-no-temp-body")
    finally:
        client.close()
        accepted.close()
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate()


def readonly(binary, env, db):
    # A read-only database and a concurrent WAL writer each reject initialization writes.
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    db.chmod(0o444)
    try:
        exact(
            binary,
            env,
            request("GET", "/api/health"),
            expected(200, HEALTH),
            "exchange-no-initialization-writes",
        )
    finally:
        db.chmod(0o600)
        for suffix in ("-wal", "-shm"):
            companion = Path(str(db) + suffix)
            if companion.exists():
                companion.chmod(0o600)
    check(
        hashlib.sha256(db.read_bytes()).hexdigest() == before,
        "exchange-no-initialization-writes",
    )
    with sqlite3.connect(db, timeout=1) as writer:
        writer.execute("begin immediate")
        exact(
            binary,
            env,
            request("GET", "/api/health"),
            expected(200, HEALTH),
            "exchange-under-writer-lock",
        )
        writer.rollback()


def copy_mutant(work, name, relative, old, new):
    root = work / name
    root.mkdir()
    (root / ".mutant-workdir").write_text(name)
    require_workdir(root, root=ROOT)
    for part in ("core", "app/native"):
        shutil.copytree(BACKEND / part, root / "backend-dawn" / part)
    path = root / "backend-dawn" / relative
    text = path.read_text()
    check(text.count(old) == 1, "negative-anchor", (name, old))
    path.write_text(text.replace(old, new))
    return root / "backend-dawn/app/native"


def controls(work, dawnc, env, db):
    variants = [
        (
            "startup-println",
            "app/native/src/main.dawn",
            "      required(maintenance.check_schema(db))",
            '      io.println("starting")\n      required(maintenance.check_schema(db))',
            lambda binary: exact(
                binary,
                env,
                request("GET", "/api/health"),
                expected(200, HEALTH),
                "stdout-is-wire",
            ),
            "stdout-is-wire",
        ),
        (
            "swallowed-guard",
            "core/src/api/webdav.dawn",
            "  req => dav_auth(a, req)",
            "  req => {\n    let _ignored = dav_auth(a, req)\n    Ok(())\n  }",
            lambda binary: early(binary, env, pending=True),
            "guard-cancel-propagates",
        ),
        (
            "immediate-close",
            "app/native/src/main.dawn",
            '      match finish_exchange() { Ok(_) -> ()\n        Err(e) -> io.eprintln("transport teardown: ${e.kind}") }',
            "      let _unused = finish_exchange",
            lambda binary: early(binary, env),
            "guard-cancel-propagates",
        ),
        (
            "repeated-initialization",
            "app/native/src/main.dawn",
            "      required(maintenance.check_schema(db))",
            "      required(maintenance.migrate(db))\n      required(maintenance.check_schema(db))",
            lambda binary: readonly(binary, env, db),
            "exchange-no-initialization-writes",
        ),
    ]
    for name, relative, old, new, probe, reason in variants:
        project = copy_mutant(work, name, relative, old, new)
        binary = project.parent.parent.parent / "mutant"
        command([dawnc, "build", project, "-o", binary])
        try:
            probe(binary)
        except AssertionError as error:
            check(reason in str(error), "negative-owner", (name, error))
            (work / f"{name}.log").write_text(str(error))
            passed(f"negative {name}: built, red by {reason}")
            # Killing the deliberately broken guard can strand its unauthorized
            # spill. It belongs to this control's private temporary directory.
            for artifact in Path(env["TMPDIR"]).iterdir():
                if artifact.is_dir():
                    shutil.rmtree(artifact)
                else:
                    artifact.unlink()
        else:
            raise AssertionError("negative survived: " + name)


def raw_cases(binary, env):
    cases = [
        ("health", request("GET", "/api/health"), expected(200, HEALTH)),
        ("head", request("HEAD", "/api/health"), expected(200, HEALTH, head=True)),
        (
            "not-found",
            request("GET", "/missing"),
            expected(404, error_body("Not Found")),
        ),
        (
            "wrong-method",
            request("POST", "/api/health"),
            expected(405, error_body("Method Not Allowed")),
        ),
        (
            "bearer",
            request("GET", "/api/auth/me"),
            expected(401, error_body("无效或过期的凭证")),
        ),
        (
            "chunked",
            request(
                "GET",
                "/api/health",
                [("Transfer-Encoding", "chunked")],
                b"3\r\na\x00z\r\n2\r\n\x80\xff\r\n0\r\n\r\n",
            ),
            expected(200, HEALTH),
        ),
        (
            "truncated-body",
            request("POST", "/api/auth/login", [("Content-Length", "10")], b"x"),
            expected(400, error_body("malformed request body")),
        ),
        (
            "duplicate-length",
            request(
                "POST",
                "/api/auth/login",
                [("Content-Length", "0"), ("Content-Length", "0")],
            ),
            expected(400, error_body("duplicate Content-Length"), vary=False),
        ),
        (
            "cl-and-te",
            request(
                "POST",
                "/api/auth/login",
                [("Content-Length", "0"), ("Transfer-Encoding", "chunked")],
            ),
            expected(
                400,
                error_body("Content-Length together with Transfer-Encoding"),
                vary=False,
            ),
        ),
        (
            "body-ceiling",
            request("POST", "/api/auth/login", [("Content-Length", "9000000")]),
            expected(413, error_body("request body exceeds 8388608 bytes")),
        ),
        ("one-exchange", request("GET", "/api/health") * 2, expected(200, HEALTH)),
        ("http10", b"GET /api/health HTTP/1.0\r\n\r\n", expected(200, HEALTH)),
    ]
    for label, data, wire in cases:
        result = exact(binary, env, data, wire, "raw-" + label)
        if label == "health":
            check(b"GET /api/health -> 200" in result.stderr, "access-log-on-stderr")
        passed("raw " + label + ": exact bytes")
    result, _ = pipe(binary, env, b"")
    check(result.returncode == 0 and result.stdout == b"", "empty-stdin")
    for pending, late in [(False, False), (True, False), (False, True)]:
        early(binary, env, pending=pending, late=late)
    passed(
        "guard refusal before body, queued and late body, bounded idle teardown, no RST"
    )


def descendants(pid):
    seen, todo = set(), [pid]
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            for task in (Path("/proc") / str(current) / "task").iterdir():
                todo.extend(map(int, (task / "children").read_text().split()))
        except (OSError, ProcessLookupError):
            pass
    return seen


def rss(pid):
    total = 0
    for child in descendants(pid):
        try:
            for line in (
                (Path("/proc") / str(child) / "status").read_text().splitlines()
            ):
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
        except OSError:
            pass
    return total


def streamed(binary, env, head, body_path, output):
    client, accepted = pair()
    errors = []
    peak = 0
    with output.open("wb") as out:
        child = subprocess.Popen(
            [str(binary)],
            stdin=accepted,
            stdout=accepted,
            stderr=subprocess.PIPE,
            env=env,
        )
        accepted.close()

        def send():
            try:
                client.sendall(head)
                if body_path:
                    with body_path.open("rb") as body:
                        while chunk := body.read(65536):
                            client.sendall(chunk)
                client.shutdown(socket.SHUT_WR)
            except OSError as error:
                errors.append(error)

        samples = []
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                samples.append(rss(child.pid))
                stop.wait(0.01)

        sampler = threading.Thread(target=sample)
        sampler.start()
        worker = threading.Thread(target=send)
        worker.start()
        client.settimeout(60)
        try:
            while True:
                peak = max(peak, rss(child.pid))
                chunk = client.recv(65536)
                if not chunk:
                    break
                out.write(chunk)
            worker.join(timeout=5)
            check(not worker.is_alive() and not errors, "stream-writer", errors)
            _, log = child.communicate(timeout=10)
            check(child.returncode == 0, "stream-exit", log)
        finally:
            client.close()
            if child.poll() is None:
                child.kill()
                child.communicate()
            worker.join(timeout=5)
            stop.set()
            sampler.join(timeout=5)
        peak = max([peak, *samples])
        check(len(samples) >= 3, "resource-sampling-window", len(samples))
    return peak


def download_digest(path):
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        head = b""
        while not head.endswith(b"\r\n\r\n"):
            byte = stream.read(1)
            check(bool(byte) and len(head) < 16384, "download-header")
            head += byte
        check(b"Transfer-Encoding: chunked\r\n" in head, "download-framing", head)
        while True:
            line = stream.readline(64)
            check(line.endswith(b"\r\n"), "download-chunk-line")
            size = int(line.strip(), 16)
            if size == 0:
                check(stream.read() == b"\r\n", "download-final-chunk")
                break
            data = stream.read(size)
            check(
                len(data) == size and stream.read(2) == b"\r\n", "download-chunk-bytes"
            )
            digest.update(data)
            total += size
    return head, total, digest.hexdigest()


def broken_socket(binary, env, data, *, read_head=False):
    import struct

    client, accepted = pair()
    child = subprocess.Popen(
        [str(binary)], stdin=accepted, stdout=accepted, stderr=subprocess.PIPE, env=env
    )
    accepted.close()
    try:
        client.sendall(data)
        if read_head:
            client.settimeout(30)
            head = b""
            while not head.endswith(b"\r\n\r\n"):
                byte = client.recv(1)
                check(bool(byte), "disconnect-response-head")
                head += byte
            check(
                head.startswith(b"HTTP/1.1 200 OK"), "disconnect-response-status", head
            )
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        client.close()
        child.communicate(timeout=30)
        check(child.returncode == 0, "disconnect-resource-cleanup-exit")
        check(not list(Path(env["TMPDIR"]).iterdir()), "disconnect-temp-cleanup")
    finally:
        client.close()
        if child.poll() is None:
            child.kill()
            child.communicate()


def memory(binary, env, work, peer, db):
    peaks = {"upload": [], "download": []}
    for mib in (256, 512):
        data = work / "payload"
        digest = hashlib.sha256()
        with data.open("wb") as stream:
            for _ in range(mib * 16):
                stream.write(BLOCK)
                digest.update(BLOCK)
        size = data.stat().st_size
        peer.payload = data
        upload = work / "upload.wire"
        peaks["upload"].append(
            streamed(
                binary,
                env,
                request(
                    "PUT",
                    f"/dav/native-{mib}.bin",
                    [("Authorization", "Basic " + AUTH), ("Content-Length", str(size))],
                ),
                data,
                upload,
            )
        )
        check(
            upload.read_bytes().startswith(b"HTTP/1.1 201 Created\r\n"),
            "upload-status",
            upload.read_bytes(),
        )
        check(
            peer.last_upload[1:] == (size, digest.hexdigest()),
            "upload-full-binary-digest",
            peer.last_upload,
        )
        download = work / "download.wire"
        peaks["download"].append(
            streamed(
                binary,
                env,
                request(
                    "GET",
                    f"/dav/native-{mib}.bin",
                    [("Authorization", "Basic " + AUTH)],
                ),
                None,
                download,
            )
        )
        header, received_size, received_hash = download_digest(download)
        check(header.startswith(b"HTTP/1.1 200 OK\r\n"), "download-status", header)
        check(
            received_size == size and received_hash == digest.hexdigest(),
            "download-full-binary-digest",
        )
        if mib == 512:
            broken_socket(
                binary,
                env,
                request(
                    "GET",
                    f"/dav/native-{mib}.bin",
                    [("Authorization", "Basic " + AUTH)],
                ),
                read_head=True,
            )
            passed(
                "actual TCP reset during 512 MiB download closes the response source and removes staging"
            )
        check(not list(Path(env["TMPDIR"]).iterdir()), "stream-temp-cleanup")
        upload.unlink()
        download.unlink()
        data.unlink()
    for direction, values in peaks.items():
        check(
            max(values) < 64 * 1024 and values[1] - values[0] < 16 * 1024,
            "entry-constant-memory",
            (direction, values),
        )
    passed(
        "256/512 MiB full binary upload/download, process tree RSS KiB=" + str(peaks)
    )
    return peaks


def disconnect(binary, env, work):
    # A partial authorized upload ends at EOF, releases its spilled body and exits.
    data = request(
        "PUT",
        "/dav/incomplete.bin",
        [("Authorization", "Basic " + AUTH), ("Content-Length", "1048576")],
        BLOCK[:100],
    )
    result, _ = pipe(binary, env, data)
    check(
        result.returncode == 0 and result.stdout.startswith(b"HTTP/1.1 400"),
        "upload-disconnect",
    )
    broken_socket(binary, env, data)
    check(not list(Path(env["TMPDIR"]).iterdir()), "disconnect-temp-cleanup")
    client, accepted = pair()
    child = subprocess.Popen(
        [str(binary)], stdin=accepted, stdout=accepted, stderr=subprocess.PIPE, env=env
    )
    accepted.close()
    client.sendall(request("GET", "/api/health"))
    client.close()
    child.communicate(timeout=5)
    check(child.returncode == 0, "response-disconnect-exit")
    passed("partial upload and downstream disconnect exit with no body temp leak")


def cold(binary, env, db):
    samples = []
    for _ in range(100):
        result, elapsed = pipe(binary, env, request("GET", "/api/health"))
        check(
            result.returncode == 0 and result.stdout == expected(200, HEALTH),
            "cold-wire",
        )
        samples.append(elapsed)
    form = (
        "username="
        + contract_fixture.FIXTURE_USER
        + "&password="
        + contract_fixture.FIXTURE_PW
    ).encode()
    login, _ = pipe(
        binary,
        env,
        request(
            "POST",
            "/api/auth/login",
            [
                ("Content-Length", str(len(form))),
                ("Content-Type", "application/x-www-form-urlencoded"),
            ],
            form,
        ),
    )
    token = json.loads(login.stdout.split(b"\r\n\r\n", 1)[1])["access_token"]
    monitor = request("GET", "/api/monitor", [("Authorization", "Bearer " + token)])
    with sqlite3.connect(db) as connection:
        connection.execute("delete from runtime_cache where namespace='cpu.v1'")
    first, cold_ms = pipe(binary, env, monitor)
    warm, warm_ms = pipe(binary, env, monitor)
    check(
        first.returncode == warm.returncode == 0
        and first.stdout.startswith(b"HTTP/1.1 200"),
        "monitor-cold",
    )
    check(cold_ms >= 300, "cpu-cold-window-included", cold_ms)
    ordered = sorted(samples)
    numbers = {
        "health_n": len(samples),
        "health_p50_ms": statistics.median(samples),
        "health_p95_ms": ordered[94],
        "health_max_ms": max(samples),
        "monitor_cpu_cold_ms": cold_ms,
        "monitor_cpu_warm_ms": warm_ms,
        "includes_process_config_db_and_exit": True,
        "cpu_cold_window_ms": 300,
    }
    passed("single exchange cold start " + json.dumps(numbers, sort_keys=True))
    return numbers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--skip-memory", action="store_true")
    args = parser.parse_args()
    work = args.work or Path(tempfile.mkdtemp(prefix="dawnop-native-entry-"))
    work.mkdir(parents=True, exist_ok=True)
    check(not any(work.iterdir()), "empty-workdir")
    started = time.monotonic()
    dawnc = Path(
        command([ROOT / "scripts/fetch-dawn-native.sh"]).stdout.decode().strip()
    )
    binary = work / "dawnop-native"
    command([dawnc, "check", BACKEND / "app/native"])
    errors, packages, files = check_core.closure_errors(
        BACKEND / "app/native", (BACKEND / "core",)
    )
    check(not errors, "native-zero-java-closure", errors)
    command([dawnc, "build", BACKEND / "app/native", "-o", binary])
    tests = command([dawnc, "test", BACKEND / "app/native"])
    (work / "native-tests.log").write_bytes(tests.stdout + tests.stderr)
    passed(
        f"native check/build/test and zero Java closure: {packages} packages, {files} sources"
    )
    db = work / "contract.db"
    contract_fixture.build(str(db))
    temp = work / "tmp"
    temp.mkdir()
    peer = Peer(work)
    try:
        envfile = work / "entry.env"
        contract_run.env_file(envfile, db, 1, peer.base)
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("DAWN_", "QINIU_", "TENCENT_"))
            and key not in {"SECRET_KEY", "DAWNOP_ENV", "LIGHTHOUSE_INSTANCE_ID"}
        }
        env.update(
            DAWNOP_ENV=str(envfile),
            TMPDIR=str(temp),
            TZ="UTC",
            NO_PROXY="*",
            no_proxy="*",
        )
        missing = work / "missing.db"
        absent, _ = pipe(
            binary, {**env, "DAWN_DB_PATH": str(missing)}, request("GET", "/api/health")
        )
        check(
            absent.returncode != 0 and not absent.stdout and not missing.exists(),
            "missing-db-is-not-created",
        )
        with sqlite3.connect(db) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        before, _ = pipe(binary, env, request("GET", "/api/health"))
        check(
            before.returncode != 0 and not before.stdout,
            "unprepared-database-fails-before-wire",
        )
        prepared, _ = pipe(binary, env, argv=["--migrate"])
        check(
            prepared.returncode == 0 and not prepared.stdout,
            "migration-is-separate",
            prepared.stderr,
        )
        controls(work, dawnc, env, db)
        raw_cases(binary, env)
        readonly(binary, env, db)
        disconnect(binary, env, work)
        numbers = cold(binary, env, db)
        peaks = {} if args.skip_memory else memory(binary, env, work, peer, db)
        cleaned, _ = pipe(binary, env, argv=["--cleanup-pastes"])
        check(
            cleaned.returncode == 0 and not cleaned.stdout,
            "cleanup-is-separate",
            cleaned.stderr,
        )
        check(not peer.errors, "fake-peer", peer.errors)
        summary = {
            "cold_start": numbers,
            "rss_kib": peaks,
            "native_packages": packages,
            "native_sources": files,
            "wall_s": time.monotonic() - started,
        }
        (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        passed(f"native entry acceptance wall_s={summary['wall_s']:.3f}")
    finally:
        peer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
