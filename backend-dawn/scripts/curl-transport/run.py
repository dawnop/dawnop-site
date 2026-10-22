"""Exercise real JVM/curl owners with external peers, resource samples and mutants."""

import argparse
import contextlib
import hashlib
import http.client
import importlib.util
import json
import os
import platform
import resource
import shutil
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BACKEND = ROOT / "backend-dawn"
SPEC = importlib.util.spec_from_file_location(
    "sqlite_acceptance", HERE.parent / "native-sqlite/run.py"
)
SQL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SQL)
run, ok = SQL.run, SQL.ok
BLOCK = bytes(range(256)) * 256
HEAD = b"multipart-head\r\n\0"
TAIL = b"\r\n\0multipart-tail"


def project(work, arm, backend=BACKEND):
    dest = work / arm
    (dest / "src").mkdir(parents=True)
    if arm == "native":
        manifest = (backend / "app/native/dawn.toml").read_text()
        # The consumer needs web directly as well as through the common package.
        manifest = manifest.replace('name = "site_sqlite"', 'name = "curl_probe"')
        manifest = manifest.replace(
            'site_core = "../../core"', f'site_core = "{BACKEND / "core"}"'
        )
        manifest = manifest.replace(
            "[deps]\n",
            f'[deps]\nsite_sqlite = "{backend / "app/native"}"\ntransport_scenarios = "{BACKEND / "test/transport"}"\n',
        )
        wrapper = """use std/io
use site_sqlite/util/http_curl.{with_upstream, CurlConfig}
use transport_scenarios/scenarios.{response, serve}
pub fn main() -> Unit !io = {
  let argv = args()
  let started = io.with_clock_real(() => io.now())
  let reply = with_upstream(CurlConfig { temp_parent: argv[5] }, () => response(argv))
  serve(reply, started)
}
"""
    else:
        manifest = (
            (BACKEND / "app/jvm/dawn.toml")
            .read_text()
            .replace(
                'site_core = "../../core"',
                f'site_core = "{BACKEND / "core"}"\ntransport_scenarios = "{BACKEND / "test/transport"}"',
            )
        )
        target = dest / "src/util/http_jvm.dawn"
        target.parent.mkdir()
        shutil.copy2(BACKEND / "app/jvm/src/util/http_jvm.dawn", target)
        wrapper = """use std/io
use util/http_jvm.{with_upstream, new_client}
use transport_scenarios/scenarios.{response, serve}
pub fn main() -> Unit !io = {
  let argv = args()
  let cell = new_client()
  let started = io.with_clock_real(() => io.now())
  let reply = with_upstream(cell, () => response(argv))
  serve(reply, started)
}
"""
    (dest / "dawn.toml").write_text(manifest)
    (dest / "src/main.dawn").write_text(wrapper)
    return dest


def builds(work, backend=BACKEND, native_only=False):
    dawnc = Path(run([ROOT / "scripts/fetch-dawn-native.sh"])[0].stdout.strip())
    dawn = Path(run([ROOT / "scripts/fetch-dawn.sh"])[0].stdout.strip())
    binary = work / "curl-probe"
    run([dawnc, "build", project(work, "native", backend), "-o", binary])
    commands = {"native": [str(binary)]}
    if not native_only:
        jar = work / "jvm-probe.jar"
        run([dawn, "build", project(work, "jvm"), "-o", jar])
        commands["jvm"] = ["java", "-Xms32m", "-Xmx256m", "-jar", str(jar)]
    return commands


@contextlib.contextmanager
def peer(work, tls=False):
    label = "https" if tls else "http"
    argv = [
        "python3",
        str(HERE / "fake.py"),
        "--ready",
        str(work / (label + ".port")),
        "--events",
        str(work / (label + ".events")),
    ]
    if tls:
        cert, key = work / "cert.pem", work / "key.pem"
        run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-keyout",
                key,
                "-out",
                cert,
            ]
        )
        argv += ["--cert", str(cert), "--key", str(key)]
    child = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        port = child.stdout.readline().strip()
        assert port.isdigit(), child.stderr.read()
        yield f"{'https' if tls else 'http'}://127.0.0.1:{port}"
    finally:
        child.terminate()
        child.communicate(timeout=10)


def processes(pid):
    result = [pid]
    seen = {pid}
    for current in result:
        # Proc.run may spawn from an IO worker. Linux lists children per task,
        # so looking only at the main task silently misses the curl process.
        for task in Path(f"/proc/{current}/task").glob("*/children"):
            try:
                children = task.read_text()
            except FileNotFoundError:
                continue
            for child in map(int, children.split()):
                if child not in seen:
                    result.append(child)
                    seen.add(child)
    return result


def sample(pid, staging, peak, stop):
    while not stop.is_set():
        rss = pss = 0
        for child in processes(pid):
            try:
                cmd = Path(f"/proc/{child}/cmdline").read_bytes()
                assert b"transport-fixture-secret" not in cmd, "credential in argv"
                fields = Path(f"/proc/{child}/smaps_rollup").read_text().splitlines()
                for line in fields:
                    if line.startswith("Rss:"):
                        rss += int(line.split()[1])
                    elif line.startswith("Pss:"):
                        pss += int(line.split()[1])
            except (FileNotFoundError, ProcessLookupError):
                pass
        disk = 0
        for file in staging.glob("site-curl-*/*"):
            try:
                disk += file.stat().st_size
                assert file.parent.stat().st_mode & 0o077 == 0, "nonprivate staging"
            except FileNotFoundError:
                pass
        peak["tree_rss_kib"] = max(peak.get("tree_rss_kib", 0), rss)
        peak["tree_pss_kib"] = max(peak.get("tree_pss_kib", 0), pss)
        peak["staging_bytes"] = max(peak.get("staging_bytes", 0), disk)
        stop.wait(0.005)


def request(
    command,
    mode,
    url,
    source,
    size,
    staging,
    *,
    budget=10,
    abort=False,
    env=None,
    limit=None,
):
    staging.mkdir(exist_ok=True)
    parent, child = socket.socketpair()
    parent.settimeout(90)
    peak, stop = {}, threading.Event()

    def watch():
        try:
            sample(process.pid, staging, peak, stop)
        except Exception as error:
            peak["sample_error"] = repr(error)

    def limits():
        if limit:
            resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)

    started = time.monotonic()
    process = subprocess.Popen(
        [*command, mode, url, str(source), str(budget), str(size), str(staging)],
        stdin=child,
        stdout=child,
        stderr=subprocess.PIPE,
        env=env,
        preexec_fn=limits if limit else None,
        start_new_session=True,
    )
    child.close()
    watcher = threading.Thread(target=watch)
    watcher.start()
    digest, count, small = hashlib.sha256(), 0, bytearray()
    first = None
    incomplete = False
    try:
        parent.sendall(
            b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        )
        if abort == "early":
            status = 0
            parent.close()
        else:
            reply = http.client.HTTPResponse(parent)
            reply.begin()
            status = reply.status
            while True:
                try:
                    chunk = reply.read(1 if first is None else 65536)
                except http.client.IncompleteRead as error:
                    chunk = error.partial
                    incomplete = True
                if not chunk:
                    break
                if first is None:
                    first = time.monotonic() - started
                digest.update(chunk)
                count += len(chunk)
                if len(small) < 4096:
                    small.extend(chunk[: 4096 - len(small)])
                if abort or incomplete:
                    break
            reply.close()
            parent.close()
        log = process.communicate(timeout=90)[1].decode(errors="replace")
        assert process.returncode == 0, (process.returncode, log)
    finally:
        parent.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        stop.set()
        watcher.join()
    assert "sample_error" not in peak, peak
    assert "transport-fixture-secret" not in log, "credential in log"
    assert not list(staging.iterdir()), "temporary-file-cleanup"
    timings = {}
    for line in log.splitlines():
        for label in ("HEADERS", "FIRST", "TOTAL"):
            prefix = f"TRANSPORT_{label}_NS="
            if line.startswith(prefix):
                timings["handler_" + label.lower() + "_s"] = (
                    int(line[len(prefix) :]) / 1e9
                )
    return {
        **timings,
        "incomplete": incomplete,
        "status": status,
        "count": count,
        "sha256": digest.hexdigest(),
        "body": bytes(small).decode(errors="replace"),
        "ttfb_s": first,
        "total_s": time.monotonic() - started,
        **peak,
        "log": log,
    }


def expected(count, head=b"", tail=b"", offset=0):
    digest = hashlib.sha256(head)
    left = count
    while left:
        chunk = (BLOCK + BLOCK)[offset : offset + min(left, len(BLOCK))]
        digest.update(chunk)
        left -= len(chunk)
    digest.update(tail)
    return digest.hexdigest()


def same(a, b, label):
    for field in ("status", "count", "sha256"):
        assert a[field] == b[field], (label, field, a, b)


def events_at(path):
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def framing(event):
    assert event["content_length"] == [event["expected_length"]], "upload-framing"
    assert event["transfer_encoding"] == [], "upload-framing"
    assert event["expect"] == [], "upload-framing"


def upload_probe(
    work, command, base, suffix, source, size, arm, env, mode="upload", staging=None
):
    path = work / "http.events"
    before = len(events_at(path))
    row = request(
        command,
        mode,
        base + suffix,
        source,
        size,
        staging or work / f"stage-{arm}",
        budget=3600,
        env=env,
    )
    observed = events_at(path)[before:]
    assert len(observed) == 1, "upload-single-request"
    event = observed[0]
    target = "/" + suffix if suffix.startswith("?") else suffix or "/"
    assert event["target_hex"] == target.encode("ascii").hex(), "upload-target"
    assert event["method"] == ("PUT" if mode == "put" else "POST"), "upload-method"
    framing(event)
    assert row["status"] == 201, row
    head, tail = (b"", b"") if mode == "raw" else (HEAD, TAIL)
    assert event["content_length"] == [str(size + len(head) + len(tail))], (
        "upload-framing"
    )
    assert (
        row["body"] == f"{size + len(head) + len(tail)}:{expected(size, head, tail)}"
    ), "upload-complete"
    row["request"] = {
        key: event[key]
        for key in (
            "method",
            "target_hex",
            "content_length",
            "transfer_encoding",
            "expect",
        )
    }
    return row


def suite(work, commands, base, tls, sizes, env):
    source = work / "payload"
    source.write_bytes(BLOCK)
    rows = []
    cases = [
        ("text", "/text", 200),
        ("bad-header", "/text", 502),
        ("stream", "/empty", 204),
        ("post", "/text", 201),
        ("start", "/text", 200),
        ("bytes", "/binary", 200),
        ("raw", "/upload", 201),
        ("upload", "/upload", 201),
        ("stream", "/binary", 200),
        ("stream", "/range", 206),
        ("stream", "/chunked", 200),
        ("text", "/informational", 201),
        ("stream", "/denied", 401),
        ("reject", "/denied", 401),
        ("bytes", "/error", 503),
        ("text", "/redirect", 302),
        ("head", "/large", 200),
        ("text", "/quiet", 504),
        ("start", "/quiet", 504),
        ("text", "/short", 502),
        ("text", "/peer-close", 502),
        ("stream", "/short", 502),
    ]
    for mode, path, status in cases:
        replies = {}
        for arm, command in commands.items():
            row = request(
                command,
                mode,
                base + path,
                source,
                len(BLOCK),
                work / f"stage-{arm}",
                budget=1 if path == "/quiet" else 10,
                env=env,
            )
            # A live JVM source has already committed 200 before detecting a
            # short response body. The staged curl arm fails before commitment.
            if mode == "stream" and path == "/short" and arm == "jvm":
                assert (
                    row["status"] == 200
                    and row["count"] == 10
                    and "stream body" in row["log"]
                    and row["incomplete"]
                ), row
            else:
                assert not row["incomplete"], row
                assert row["status"] == status, (arm, mode, path, row)
            if path == "/range":
                assert row["sha256"] == hashlib.sha256(BLOCK[7:264]).hexdigest(), (
                    "range-forwarded"
                )
            if path == "/binary":
                assert row["sha256"] == hashlib.sha256(BLOCK).hexdigest(), (
                    "binary-complete"
                )
            if mode in ("raw", "upload"):
                digest = hashlib.sha256(
                    (HEAD if mode == "upload" else b"")
                    + BLOCK
                    + (TAIL if mode == "upload" else b"")
                ).hexdigest()
                length = len(BLOCK) + (len(HEAD) + len(TAIL) if mode == "upload" else 0)
                assert row["body"] == f"{length}:{digest}", "upload-complete"
            replies[arm] = row
            rows.append({"arm": arm, "case": mode + path, **row})
        if "jvm" in replies and not (mode == "stream" and path == "/short"):
            same(replies["native"], replies["jvm"], mode + path)
        ok(f"transport dual arm {mode} {path}: {status}")
    for mode in ("upload", "raw", "put"):
        for suffix in (
            "/",
            "/upload/",
            "/upload/?key=a%2Fb&sig=A%2B%2f%3D&x=one+two&x=&bare&encoded=%252F",
            "/a%2fb/%7e/./../end//?sig=Z%2bz&space=%20&raw=;:@%24&dupe=1&dupe=2",
            "?root=1&signature=%2f%2F%3d",
        ):
            replies = {}
            for arm, command in commands.items():
                row = upload_probe(
                    work, command, base, suffix, source, len(BLOCK), arm, env, mode
                )
                rows.append({"arm": arm, "case": mode + "-target:" + suffix, **row})
                replies[arm] = row
            same(replies["native"], replies["jvm"], "exact target " + suffix)
            assert replies["native"]["request"] == replies["jvm"]["request"], (
                "dual-arm-upload-headers"
            )
    ok(
        "dual arm POST/PUT raw/multipart: exact request-line target, trailing slash, encoded query, length, no chunked or Expect"
    )
    for arm, command in commands.items():
        for url in [tls + "/", "http://127.0.0.1:1/"]:
            row = request(
                command, "text", url, source, 0, work / f"stage-{arm}", env=env
            )
            assert row["status"] == 502, "transport-failure-not-200"
        for path in ["/oversize", "/oversize-chunked"]:
            row = request(
                command, "text", base + path, source, 0, work / f"stage-{arm}", env=env
            )
            if arm == "native":
                assert row["status"] == 502, "management-size-limit"
            else:
                assert row["status"] == 200 and row["count"] == 8388608
        # A kernel-enforced file quota exercises real write failures without
        # mounting or filling shared disks. JVM streaming needs no staging.
        for mode, path in [("stream", "/large"), ("upload", "/upload")]:
            row = request(
                command,
                mode,
                base + path,
                source,
                len(BLOCK),
                work / f"stage-{arm}",
                env=env,
                limit=16384,
            )
            assert row["status"] == (
                502 if arm == "native" else 200 if mode == "stream" else 201
            ), row
            rows.append({"arm": arm, "case": "disk-full-" + mode, **row})
    ok("TLS, refusal, management known/chunked limits and kernel disk quota")
    # A real file slice is supported by curl; the existing JVM owner explicitly
    # refuses it. Do not weaken that documented S7 boundary to manufacture parity.
    row = request(
        commands["native"],
        "slice",
        base + "/upload",
        source,
        1000,
        work / "stage-native",
        env=env,
    )
    assert (
        row["body"]
        == f"{1000 + len(HEAD) + len(TAIL)}:{hashlib.sha256(HEAD + BLOCK[17:1017] + TAIL).hexdigest()}"
    )
    for size in sizes:
        count = size * 1024 * 1024
        with source.open("wb") as target:
            for _ in range(count // len(BLOCK)):
                target.write(BLOCK)
        for arm, command in commands.items():
            for mode in ("upload", "stream"):
                if mode == "upload":
                    row = upload_probe(
                        work,
                        command,
                        base,
                        "/upload/?signed=a%2Fb&v=1&v=2",
                        source,
                        count,
                        arm,
                        env,
                    )
                else:
                    row = request(
                        command,
                        mode,
                        base + f"/large?bytes={count}",
                        source,
                        count,
                        work / f"stage-{arm}",
                        budget=3600,
                        env=env,
                    )
                assert row["status"] == (201 if mode == "upload" else 200)
                if mode == "upload":
                    assert (
                        row["body"]
                        == f"{count + len(HEAD) + len(TAIL)}:{expected(count, HEAD, TAIL)}"
                    ), "large-upload-digest"
                else:
                    assert row["count"] == count and row["sha256"] == expected(count), (
                        "large-download-digest"
                    )
                rows.append({"arm": arm, "case": f"{size}MiB-{mode}", **row})
                ok(
                    f"{arm} {size}MiB {mode}: ttfb={row['ttfb_s']:.3f}s total={row['total_s']:.3f}s treeRSS={row['tree_rss_kib']}KiB treePSS={row['tree_pss_kib']}KiB"
                )
    for size in sizes:
        pair = {
            r["arm"]: r["request"] for r in rows if r["case"] == f"{size}MiB-upload"
        }
        assert pair["native"] == pair["jvm"], "dual-arm-upload-headers"
    for arm, command in commands.items():
        row = request(
            command,
            "stream",
            base + "/slow?bytes=8388608",
            source,
            0,
            work / f"stage-{arm}",
            budget=3600,
            env=env,
        )
        assert row["sha256"] == expected(8388608)
        rows.append({"arm": arm, "case": "slow-download", **row})
        row = request(
            command,
            "stream",
            base + "/large?bytes=16777216",
            source,
            0,
            work / f"stage-{arm}",
            env=env,
            abort=True,
        )
        assert row["count"] == 1
        early = request(
            command,
            "stream",
            base + "/slow?bytes=8388608",
            source,
            0,
            work / f"stage-{arm}",
            budget=3600,
            env=env,
            abort="early",
        )
        rows.append({"arm": arm, "case": "disconnect-before-headers", **early})
        rows.append({"arm": arm, "case": "client-disconnect", **row})
        row = request(
            command,
            "upload",
            base + "/early",
            source,
            source.stat().st_size,
            work / f"stage-{arm}",
            budget=3600,
            env=env,
        )
        assert row["status"] == 401, row
        rows.append({"arm": arm, "case": "early-401", **row})
        row = request(
            command,
            "upload",
            base + "/slow-upload",
            source,
            source.stat().st_size,
            work / f"stage-{arm}",
            budget=1,
            env=env,
        )
        assert row["status"] == 504, row
    ok("slow source, actual socket disconnect, early 401 and upload hard deadline")
    return rows


def disk_full(work, commands, base, env):
    mount = work / "private-tmpfs"
    mount.mkdir()
    source = work / "disk-payload"
    source.write_bytes(BLOCK * 16)
    settings = work / "disk-settings.json"
    results = work / "disk-results.json"
    settings.write_text(
        json.dumps(
            {
                "mount": str(mount),
                "commands": commands,
                "source": str(source),
                "base": base,
                "env": {
                    key: env[key]
                    for key in (
                        "CURL_HOME",
                        "http_proxy",
                        "HTTPS_PROXY",
                        "ALL_PROXY",
                        "NO_PROXY",
                        "PATH",
                        "JAVA_HOME",
                    )
                    if key in env
                },
                "results": str(results),
            }
        )
    )
    # GitHub runners restrict unprivileged user namespaces through AppArmor;
    # sudo is available there. Both variants mount only in a new namespace,
    # never in the host mount table, and unmount in a finally block.
    prefix = (
        ["sudo", "-n", "unshare", "--mount", "--propagation", "private"]
        if os.environ.get("GITHUB_ACTIONS") == "true"
        else [
            "unshare",
            "--user",
            "--map-root-user",
            "--mount",
            "--propagation",
            "private",
        ]
    )
    try:
        run([*prefix, "python3", HERE / "disk.py", settings])
        rows = json.loads(results.read_text())
    finally:
        settings.unlink()
    assert not list(mount.iterdir())
    ok(
        "real ENOSPC in private 128KiB tmpfs: native upload/download fail 502, JVM streaming succeeds; all files removed"
    )
    return rows


def mutant(work, name, before, after):
    target = work / name
    backend = target / "backend-dawn"
    shutil.copytree(BACKEND / "app/native", backend / "app/native")
    manifest = backend / "app/native/dawn.toml"
    manifest.write_text(
        manifest.read_text().replace(
            'site_core = "../../core"', f'site_core = "{BACKEND / "core"}"'
        )
    )
    (backend / SQL.GUARD.SENTINEL).touch()
    SQL.GUARD.require_workdir(backend)
    file = backend / "app/native/src/util/http_curl.dawn"
    text = file.read_text()
    assert text.count(before) == 1, (name, text.count(before))
    file.write_text(text.replace(before, after))
    return target, builds(target, backend, native_only=True)


def controls(work, base, env):
    work.mkdir(parents=True)
    cases = [
        (
            "timeout-as-502",
            'fault("TimeoutException", "curl deadline expired")',
            'fault("TransferError", "curl deadline expired")',
            "text",
            "/quiet",
            504,
            "timeout-classification",
        ),
        (
            "binary-at-nul",
            "bytes: raw",
            'bytes: bytes.slice(raw, 0, unwrap_or(bytes.index_of(raw, bytes.utf8("\\u{0}")), len(raw)))',
            "bytes",
            "/binary",
            200,
            "binary-complete",
        ),
        (
            "drop-range",
            'headers = headers ++ name ++ ": " ++ value ++ "\\n"',
            'if str.to_lower(name) != "range" { headers = headers ++ name ++ ": " ++ value ++ "\\n" }',
            "stream",
            "/range",
            206,
            "range-forwarded",
        ),
        (
            "failure-as-200",
            "if code != 0 { return Err(exit_error(code)) }",
            'if code != 0 {\n    io.write_bytes(dir ++ "/body", bytes.utf8(""))?\n    return Ok(200)\n  }',
            "text",
            "/short",
            502,
            "transport-failure-not-200",
        ),
        (
            "leak-temp",
            "close: () => cleanup(staged.dir)",
            "close: () => ()",
            "stream",
            "/binary",
            200,
            "temporary-file-cleanup",
        ),
    ]
    cases.extend(
        [
            (
                "drop-request-target",
                ' ++ option("request-target", request_target(req.url))',
                "",
                "upload",
                "/",
                201,
                "upload-target",
            ),
            (
                "allow-expect",
                r'"Expect:\n"',
                '""',
                "upload",
                "/upload",
                201,
                "upload-framing",
            ),
            (
                "wrong-content-length",
                'to_string(io.file_size(dir ++ "/upload")?)',
                'to_string(io.file_size(dir ++ "/upload")? + 1)',
                "upload",
                "/upload",
                201,
                "upload-framing",
            ),
        ]
    )
    payload = work / "payload"
    payload.write_bytes(BLOCK)
    for name, before, after, mode, path, status, reason in cases:
        target, commands = mutant(work, name, before, after)
        payload.write_bytes(BLOCK * (32 if name == "allow-expect" else 1))
        event_offset = len(events_at(work.parent / "http.events"))
        stage = target / "stage"
        try:
            row = request(
                commands["native"],
                mode,
                base + path,
                payload,
                payload.stat().st_size,
                stage,
                budget=1 if path == "/quiet" else 10,
                env=env,
            )
            if reason in ("upload-target", "upload-framing"):
                observed = events_at(work.parent / "http.events")[event_offset:]
                assert len(observed) == 1, "upload-single-request"
                if reason == "upload-target":
                    assert observed[0]["target_hex"] == path.encode("ascii").hex(), (
                        reason
                    )
                else:
                    framing(observed[0])
            assert row["status"] == status, reason
            if path == "/binary":
                assert row["sha256"] == hashlib.sha256(BLOCK).hexdigest(), reason
            if path == "/range":
                assert row["sha256"] == hashlib.sha256(BLOCK[7:264]).hexdigest(), reason
        except AssertionError as error:
            assert str(error) == reason, (name, str(error))
            (target / "negative.log").write_text(
                f"compiled successfully; red by {reason}\n"
            )
            ok(f"negative {name}: compiled successfully; red by {reason}")
        else:
            raise AssertionError("negative control stayed green: " + name)
        # A deliberately leaked body is evidence, then explicitly removed from
        # this disposable consumer. Mutators never write production worktrees.
        shutil.rmtree(stage)


def buffered_upload_control(work, base, env):
    name = "buffered-upload"
    target, commands = mutant(
        work,
        name,
        'option("upload-file", dir ++ "/upload")',
        'option("data-binary", "@" ++ dir ++ "/upload")',
    )
    payload = target / "payload"
    rows = []
    for size in (256, 512):
        count = size * 1024 * 1024
        with payload.open("wb") as out:
            for _ in range(count // len(BLOCK)):
                out.write(BLOCK)
        row = upload_probe(
            work.parent,
            commands["native"],
            base,
            "/upload/?control=buffered",
            payload,
            count,
            "native",
            env,
            staging=target / "stage",
        )
        rows.append({"arm": "native", "case": f"{size}MiB-upload", **row})
        ok(
            f"buffering negative {size}MiB: full digest and framing pass; treeRSS={row['tree_rss_kib']}KiB treePSS={row['tree_pss_kib']}KiB"
        )
    (target / "results.json").write_text(json.dumps(rows, indent=2))
    try:
        memory_guard(rows)
    except AssertionError as error:
        assert str(error) == "native-upload-memory", str(error)
        (target / "negative.log").write_text(
            "compiled successfully; full digest/framing pass; red by native-upload-memory\n"
        )
        ok(
            "negative buffered-upload: compiled successfully; full digests/framing pass; red by unchanged native-upload-memory gate"
        )
    else:
        raise AssertionError("buffering negative control stayed green")


def memory_guard(rows):
    for arm, ceiling in [("native", 65536), ("jvm", 393216)]:
        for mode in ("upload", "stream"):
            large = [
                r
                for r in rows
                if r["arm"] == arm and r["case"] in (f"256MiB-{mode}", f"512MiB-{mode}")
            ]
            if not large:
                continue
            assert all(r["tree_rss_kib"] < ceiling for r in large), (
                arm + "-" + mode + "-memory"
            )
            # A fixed envelope plus the full digests prevents an O(file-size)
            # implementation or a truncated transfer from passing as bounded.
            assert max(r["tree_rss_kib"] for r in large) - min(
                r["tree_rss_kib"] for r in large
            ) < (16384 if arm == "native" else 98304), (
                arm + "-" + mode + "-memory-growth"
            )
    ok("large transfer process-tree memory envelopes and growth bounds")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--sizes", default="256,512")
    parser.add_argument("--skip-controls", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    assert not any(work.iterdir()), "work directory must be empty"
    commands = builds(work)
    poison = work / "curl-home"
    poison.mkdir()
    (poison / ".curlrc").write_text('header = "X-Poison: true"\nlocation\ninsecure\n')
    env = {
        **os.environ,
        "CURL_HOME": str(poison),
        "http_proxy": "http://127.0.0.1:1",
        "HTTPS_PROXY": "http://127.0.0.1:1",
        "ALL_PROXY": "http://127.0.0.1:1",
        "NO_PROXY": "",
    }
    with peer(work) as base, peer(work, tls=True) as tls:
        if not args.skip_controls:
            controls(work / "controls", base, env)
            buffered_upload_control(work / "controls", base, env)
        event_offset = len(events_at(work / "http.events"))
        rows = suite(
            work, commands, base, tls, [int(n) for n in args.sizes.split(",")], env
        )
        rows.extend(disk_full(work, commands, base, env))
        events = [
            json.loads(line) for line in (work / "http.events").read_text().splitlines()
        ]
        assert all(not e["poisoned"] and e["auth"] for e in events)
        for event in events[event_offset:]:
            if event["expected_length"] is not None:
                framing(event)
        assert not any(e["method"] == "HEAD" and e["path"] != "/large" for e in events)
    for arm in commands:
        for mode in ("upload", "stream"):
            selected = [
                r for r in rows if r["arm"] == arm and r["case"].endswith("MiB-" + mode)
            ]
            for row in selected:
                assert row["tree_rss_kib"] > 0 and row["tree_pss_kib"] > 0, (
                    "missing resource samples"
                )
    report = {
        "wall_s": time.monotonic() - started,
        "machine": platform.platform(),
        "curl": run(["curl", "--version"])[0].stdout.splitlines()[0],
        "rows": rows,
    }
    (work / "results.json").write_text(json.dumps(report, indent=2))
    memory_guard(rows)
    ok("transport tool: " + report["curl"])
    for row in rows:
        if "MiB" in row["case"] or row["case"] == "slow-download":
            ok(
                f"handler timing {row['arm']} {row['case']}: first_s={row['handler_first_s']:.6f} total_s={row['handler_total_s']:.6f} disk_bytes={row['staging_bytes']}"
            )
    ok(
        f"curl transport acceptance; wall_s={report['wall_s']:.3f}; results={work / 'results.json'}"
    )


if __name__ == "__main__":
    main()
