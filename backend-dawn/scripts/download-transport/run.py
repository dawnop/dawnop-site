"""Actual client -> isolated nginx -> production native ELF -> fake TLS object.

Gates are fixed before measurement. No production configuration is consumed.
Only summary/count/digest evidence survives cleanup, including on failure.
"""

import argparse
import concurrent.futures
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
import contract_fixture  # noqa: E402
import contract_native  # noqa: E402
import contract_qiniu_fake  # noqa: E402
import contract_run  # noqa: E402
from dav_proof import prove  # noqa: E402
from fake import BLOCK, ETAG, MODIFIED, OLD, Peer  # noqa: E402
from nginx_config import render  # noqa: E402

GATES = {
    "body_p95_ms": 250,
    "jvm_margin_ms": 100,
    "stage_ratio": 4,
    "disk_bytes_per_request": 1048576,
    "memory_growth_kib_per_request": 16384,
    "cancel_s": 2,
    "rounds": 10,
}


def check(ok, label):
    if not ok:
        raise AssertionError(label)


def command(argv, **kwargs):
    p = subprocess.run(list(map(str, argv)), capture_output=True, timeout=300, **kwargs)
    check(
        p.returncode == 0,
        "command failed: "
        + Path(str(argv[0])).name
        + " "
        + p.stderr.decode(errors="replace")[-1500:],
    )
    return p


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(number, process):
    for _ in range(150):
        check(process.poll() is None, "local process exited")
        try:
            with socket.create_connection(("127.0.0.1", number), timeout=0.1):
                return
        except OSError:
            time.sleep(0.02)
    raise AssertionError("local listener timeout")


@contextmanager
def nginx(root, config):
    root.mkdir(exist_ok=True)
    cfg = root / "nginx.conf"
    cfg.write_text(config)
    command(["nginx", "-e", "/dev/null", "-t", "-p", str(root) + "/", "-c", cfg])
    p = subprocess.Popen(
        ["nginx", "-e", "/dev/null", "-p", str(root) + "/", "-c", str(cfg)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield p
    finally:
        p.terminate()
        p.wait(timeout=5)
        cfg.unlink()


def fetch(
    number, token, *, key="payload", headers=None, method="GET", path=None, slow=False
):
    start = time.monotonic()
    connection = http.client.HTTPConnection("127.0.0.1", number, timeout=8)
    fields = {
        "Authorization": "Bearer " + token,
        "Origin": "https://dawnop.com",
        **(headers or {}),
    }
    connection.request(
        method, path or "/api/fm/content?" + urlencode({"path": key}), headers=fields
    )
    # These isolated GET/HEAD fixtures emit no informational responses. Peek
    # the first final-status byte without consuming it from the HTTP parser.
    connection.sock.recv(1, socket.MSG_PEEK)
    head = time.monotonic() - start
    response = connection.getresponse()
    digest = hashlib.sha256()
    first = None
    size = 0
    preview = b""
    complete = True
    try:
        while True:
            chunk = response.read1(65536)
            if not chunk:
                break
            if first is None:
                first = time.monotonic() - start
            digest.update(chunk)
            size += len(chunk)
            if len(preview) < 128:
                preview += chunk[: 128 - len(preview)]
            if slow:
                time.sleep(0.001)
        if method != "HEAD" and response.length not in {None, 0}:
            complete = False
    except (OSError, http.client.HTTPException):
        complete = False
    result = {
        "status": response.status,
        "headers": response.getheaders(),
        "bytes": size,
        "sha256": digest.hexdigest(),
        "head_ms": head * 1000,
        "body_ms": (first or head) * 1000,
        "total_ms": (time.monotonic() - start) * 1000,
        "complete": complete,
        "preview": preview,
    }
    connection.close()
    return result


def header(r, name):
    values = [v for k, v in r["headers"] if k.lower() == name.lower()]
    check(len(values) <= 1, "duplicate " + name)
    return values[0] if values else None


def wire(r):
    for name in (
        "Content-Length",
        "ETag",
        "Last-Modified",
        "Content-Type",
        "Content-Range",
    ):
        header(r, name)
    for name, value in r["headers"]:
        check(
            not name.lower().startswith(("x-accel-", "x-dawn-")),
            "internal header leaked",
        )
        check(
            "token=" not in value and "redirect.invalid" not in value,
            "capability leaked",
        )
    check(header(r, "Location") is None, "source redirect leaked")


def expected_digest(size):
    h = hashlib.sha256()
    while size:
        count = min(size, len(BLOCK))
        h.update(BLOCK[:count])
        size -= count
    return h.hexdigest()


def seed(db, keys):
    with sqlite3.connect(db) as c:
        for key in keys:
            c.execute(
                'INSERT OR REPLACE INTO files (path,is_dir,"key",content_type,size,created_at,updated_at) VALUES (?,0,?,?,7,?,?)',
                (
                    key,
                    key,
                    "text/plain\r\nInjected: yes"
                    if key == "dirty"
                    else "application/octet-stream",
                    contract_fixture.T0,
                    contract_fixture.T0,
                ),
            )


def login(number):
    c = http.client.HTTPConnection("127.0.0.1", number, timeout=10)
    c.request(
        "POST",
        "/api/auth/login",
        urlencode(
            {
                "username": contract_fixture.FIXTURE_USER,
                "password": contract_fixture.FIXTURE_PW,
            }
        ),
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    r = c.getresponse()
    check(r.status == 200, "fixture login")
    token = json.loads(r.read())["access_token"]
    c.close()
    return token


def quantiles(rows):
    return {
        name: {
            "p50": statistics.median(r[name] for r in rows),
            "p95": sorted(r[name] for r in rows)[int(0.95 * (len(rows) - 1)) + 1],
        }
        for name in ("head_ms", "body_ms", "total_ms")
    }


def handshake_timeout(root, config, number, token, peer):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    observed = {}

    def accept():
        accepted, _ = listener.accept()
        with accepted:
            accepted.settimeout(3)
            accepted.recv(4096)  # TLS ClientHello, deliberately no ServerHello.
            observed["hello"] = True
            observed["closed"] = accepted.recv(1) == b""

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    replacement = f"server 127.0.0.1:{listener.getsockname()[1]};"
    variant = config.replace(f"server 127.0.0.1:{peer.server_port};", replacement)
    try:
        with nginx(root / "connect-timeout", variant) as p:
            wait_port(number, p)
            r = fetch(number, token)
            check(r["status"] == 504, "TLS connect budget maps to 504 before HTTP")
        thread.join(timeout=4)
        check(
            observed.get("hello") and observed.get("closed"),
            "connect timeout closes fake TLS peer",
        )
        return {"status": 504, "final_head_ms": r["head_ms"], "peer_closed": True}
    finally:
        listener.close()


def protocol(number, token, peer, db, results):
    cases = []

    def case(label, expected=200, **kwargs):
        r = fetch(number, token, **kwargs)
        check(r["status"] == expected, label + " status=" + str(r["status"]))
        wire(r)
        results["protocol_progress"] = len(cases) + 1
        cases.append(label)
        print("PASS " + label, flush=True)
        return r

    before = len(peer.seen)
    case("anonymous-before-object", 401, headers={"Authorization": ""})
    case(
        "query-token-is-not-auth",
        401,
        headers={"Authorization": ""},
        path="/api/fm/content?" + urlencode({"path": "payload", "token": token}),
    )
    case("internal-is-private", 404, path="/_dawn_object")
    case(
        "forged-internal-cannot-authenticate",
        401,
        headers={
            "Authorization": "",
            "X-Accel-Redirect": "/_dawn_object",
            "X-Dawn-Download-Target": "/error-200",
        },
    )
    check(len(peer.seen) == before, "early-reject-zero-object-requests")
    r = case(
        "full-object",
        headers={
            "Cookie": "app=private",
            "X-Dawn-Download-Target": "/forged",
            "X-Dav-Prefix": "/private",
        },
    )
    check(
        r["bytes"] == peer.size and r["sha256"] == expected_digest(peer.size),
        "full-object-digest",
    )
    check(
        header(r, "Content-Type") == "application/octet-stream",
        "application MIME frozen",
    )
    check(
        header(r, "Access-Control-Allow-Origin") == "https://dawnop.com",
        "application CORS frozen",
    )
    check(
        header(r, "ETag") == ETAG and header(r, "Last-Modified") == MODIFIED,
        "object validators",
    )
    get_representation = r
    observation = peer.seen[-1]
    lower = {k.lower(): v for k, v in observation["headers"].items()}
    check(
        not any(
            k in lower
            for k in (
                "authorization",
                "cookie",
                "x-dav-prefix",
                "x-dawn-download-target",
                "content-length",
            )
        ),
        "object credentials/body isolation",
    )
    check(lower["accept-encoding"] == "identity", "identity representation")
    before = len(peer.seen)
    r = case("head-is-one-object-head", method="HEAD")
    check(
        r["bytes"] == 0
        and header(r, "Content-Length") == str(peer.size)
        and len(peer.seen) == before + 1
        and peer.seen[-1]["method"] == "HEAD",
        "HEAD ownership",
    )
    results["dav_persisted_fixture"] = prove(
        db.parent / "representations.db", get_representation, r, lambda: len(peer.seen)
    )
    case("dirty-MIME-sanitized", key="dirty")
    check(
        header(fetch(number, token, key="dirty"), "Content-Type")
        == "application/octet-stream",
        "dirty MIME",
    )
    case("object-control-headers-ignored", key="controls")
    check(peer.sni[-1] == "localhost", "signing SNI")
    encoded = "中文 space%ZZ.txt"
    case("signed-target-byte-preservation", key=encoded)
    check(
        "%E4%B8%AD%E6%96%87%20space%25ZZ.txt?e=" in peer.seen[-1]["target"],
        "signed target exact octets",
    )
    for key in ("../x", "a/../b", "a\\b", "x\r\ny", "a//b"):
        before = len(peer.seen)
        case("invalid-key-closed", 502, key=key)
        check(len(peer.seen) == before, "invalid key no object")
    for code in (
        300,
        301,
        302,
        307,
        308,
        400,
        401,
        403,
        404,
        405,
        429,
        500,
        502,
        503,
        504,
        599,
    ):
        r = case(
            "source-error-" + str(code),
            504 if code == 504 else 502,
            key="error-" + str(code),
        )
        check(
            header(r, "Content-Type") == "application/json"
            and r["preview"].startswith(b'{"detail":'),
            "narrow error JSON",
        )
        if header(r, "WWW-Authenticate"):
            results["gaps"].append(
                "object 401 challenge survives nginx error interception"
            )
    try:
        r = case("source-error-408", 502, key="error-408")
        results["source_408"] = "502"
    except http.client.RemoteDisconnected:
        results["source_408"] = "connection closed before response"
        results["gaps"].append(
            "stock nginx treats source 408 as an early connection close rather than narrow JSON 502"
        )
    r = case("source-612-gap", 612, key="error-612")
    results["gaps"].append(
        "stock nginx error_page rejects 612 configuration; upstream 612 and its source error body survive instead of narrow JSON 502"
    )
    for label, fields, status, length in (
        ("bounded", {"Range": "bytes=5-14"}, 206, 10),
        ("open", {"Range": "bytes=5-"}, 206, peer.size - 5),
        ("suffix", {"Range": "bytes=-10"}, 206, 10),
        ("unsatisfied", {"Range": "bytes=999999-"}, 416, None),
        ("malformed", {"Range": "bytes=broken"}, 200, peer.size),
        ("multiple", {"Range": "bytes=0-1,4-5"}, 200, peer.size),
        ("if-range-match", {"Range": "bytes=5-14", "If-Range": ETAG}, 206, 10),
        (
            "if-range-miss",
            {"Range": "bytes=5-14", "If-Range": '"old-key"'},
            200,
            peer.size,
        ),
        (
            "if-range-weak",
            {"Range": "bytes=5-14", "If-Range": "W/" + ETAG},
            200,
            peer.size,
        ),
        ("if-range-date", {"Range": "bytes=5-14", "If-Range": MODIFIED}, 206, 10),
        ("if-range-old-date", {"Range": "bytes=5-14", "If-Range": OLD}, 200, peer.size),
        ("none-match", {"If-None-Match": ETAG}, 304, 0),
        ("none-match-weak", {"If-None-Match": "W/" + ETAG}, 304, 0),
        (
            "none-date-priority",
            {"If-None-Match": ETAG, "If-Modified-Since": OLD},
            304,
            0,
        ),
        ("modified-date", {"If-Modified-Since": MODIFIED}, 304, 0),
        ("match-failed", {"If-Match": '"old-key"'}, 412, None),
        ("unmodified-failed", {"If-Unmodified-Since": OLD}, 412, None),
        ("match-ok", {"If-Match": ETAG}, 200, peer.size),
    ):
        r = case("entity-" + label, status, headers=fields)
        if length is not None:
            check(r["bytes"] == length, label + " length")
        if status == 206:
            content_range = re.fullmatch(
                r"bytes (\d+)-(\d+)/(\d+)", header(r, "Content-Range") or ""
            )
            check(content_range is not None, "206 range ownership")
            first, last, total = map(int, content_range.groups())
            check(
                total == peer.size and last - first + 1 == r["bytes"],
                "206 wire range and length agree",
            )
            expected_body = (BLOCK + BLOCK)[
                first % len(BLOCK) : first % len(BLOCK) + r["bytes"]
            ]
            check(
                r["sha256"] == hashlib.sha256(expected_body).hexdigest(),
                "206 wire bytes agree with range",
            )
        if status == 416:
            check(
                header(r, "Content-Range") == f"bytes */{peer.size}", "416 object size"
            )
    case("source-ignores-range", key="ignore-range", headers={"Range": "bytes=1-2"})
    saved = peer.size
    peer.size = 0
    empty_get = case("empty-object")
    empty_head = case("empty-head", method="HEAD")
    results["dav_empty_persisted_fixture"] = prove(
        db.parent / "representations-empty.db",
        empty_get,
        empty_head,
        lambda: len(peer.seen),
    )
    peer.size = saved
    for key in ("short", "stall-body"):
        r = case("incomplete-" + key, key=key)
        check(not r["complete"], "incomplete entity must not succeed")
        check(
            r["sha256"] == expected_digest(r["bytes"]),
            "no error JSON or second response appended after final head",
        )
    case("header-timeout", 504, key="stall-head")
    peer.size = 20
    r = case("progress-outlives-total-budget", key="drip")
    check(
        r["complete"] and r["total_ms"] > 1500, "no total deadline; progress survives"
    )
    peer.size = saved
    results["protocol"] = {
        "passed": len(cases),
        "cases": cases,
        "source_612_status": 612,
    }
    results["dav"] = {
        "business_routes": "unchanged",
        "entity_protocol": "shared isolated FM entity proof",
        "metadata": "DB size=7 and key/time intentionally disagree with object size/ETag/date",
        "migration": "required before DAV integration",
        "clients": "Finder and Windows not tested",
    }


def cancel(number, token, peer, *, before_headers=False):
    peer.size = 512 * 1024 * 1024
    peer.delay = 0.005
    before = len(peer.seen)
    c = http.client.HTTPConnection("127.0.0.1", number, timeout=5)
    c.request(
        "GET",
        "/api/fm/content?path=" + ("cancel-head" if before_headers else "payload"),
        headers={"Authorization": "Bearer " + token},
    )
    if before_headers:
        deadline = time.monotonic() + 2
        while len(peer.seen) == before and time.monotonic() < deadline:
            time.sleep(0.005)
    else:
        r = c.getresponse()
        check(r.status == 200 and r.read(1), "cancel started object")
    check(len(peer.seen) > before, "cancel reached source")
    observation = peer.seen[-1]
    initial = observation["bytes"]
    start = time.monotonic()
    c.close()
    if not before_headers:
        r.close()
    while observation["end"] is None and time.monotonic() - start < 3:
        time.sleep(0.01)
    elapsed = time.monotonic() - start
    check(
        observation["end"] is not None and elapsed <= GATES["cancel_s"],
        "source cancellation within 2s",
    )
    check(observation["bytes"] < 8 * 1024 * 1024, "source cancellation bounded read")
    peer.delay = 0
    return {
        "source_stop_s": elapsed,
        "extra_bytes": observation["bytes"] - initial,
        "source_bytes": observation["bytes"],
    }


def idle_and_limit(number, token, peer):
    peer.size = 512 * 1024 * 1024
    peer.delay = 0
    connections = []
    try:
        for _ in range(4):
            c = http.client.HTTPConnection("127.0.0.1", number, timeout=5)
            c.connect()
            c.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            c.request(
                "GET",
                "/api/fm/content?path=payload",
                headers={"Authorization": "Bearer " + token},
            )
            r = c.getresponse()
            check(r.status == 200, "four object transfers admitted")
            connections.append((c, r, peer.seen[-1]))
        before = len(peer.seen)
        excess = fetch(number, token)
        check(
            excess["status"] == 503 and len(peer.seen) == before,
            "fifth download rejected before object IO",
        )
        started = time.monotonic()
        while (
            any(observation["end"] is None for _, _, observation in connections)
            and time.monotonic() - started < 3
        ):
            time.sleep(0.01)
        check(
            all(
                observation["end"] is not None and observation["bytes"] < peer.size
                for _, _, observation in connections
            ),
            "downstream write idle cancels actual source",
        )
        return {
            "limit": 4,
            "fifth_status": 503,
            "idle_release_s": time.monotonic() - started,
            "source_bytes": [observation["bytes"] for _, _, observation in connections],
        }
    finally:
        for c, r, _ in connections:
            r.close()
            c.close()


def snapshot(pids, descendants=True):
    todo, found = list(pids), set()
    while todo:
        pid = todo.pop()
        if pid in found:
            continue
        found.add(pid)
        for task in Path(f"/proc/{pid}/task").glob("*/children") if descendants else []:
            try:
                todo.extend(map(int, task.read_text().split()))
            except OSError:
                pass
    totals = {"rss_kib": 0, "pss_kib": 0}
    for pid in found:
        try:
            for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
                if line.startswith("Rss:"):
                    totals["rss_kib"] += int(line.split()[1])
                if line.startswith("Pss:"):
                    totals["pss_kib"] += int(line.split()[1])
        except (OSError, ProcessLookupError):
            pass
    return totals


def temporary_bytes(directories, root_pid):
    # nginx unlinks temporary files while descriptors are still open. Count
    # those real files too; a pathname-only scan would falsely report zero.
    files = {}
    for directory in directories:
        for file in directory.rglob("*"):
            try:
                if file.is_file():
                    stat = file.stat()
                    files[(stat.st_dev, stat.st_ino)] = stat.st_size
            except OSError:
                pass
    todo, seen = [root_pid], set()
    while todo:
        pid = todo.pop()
        if pid in seen:
            continue
        seen.add(pid)
        for task in Path(f"/proc/{pid}/task").glob("*/children"):
            try:
                todo.extend(map(int, task.read_text().split()))
            except OSError:
                pass
        for descriptor in Path(f"/proc/{pid}/fd").glob("*"):
            try:
                target = os.readlink(descriptor)
                if any(str(directory) in target for directory in directories):
                    stat = descriptor.stat()
                    files[(stat.st_dev, stat.st_ino)] = stat.st_size
            except OSError:
                pass
    return sum(files.values())


def resources(number, token, peer, nginx_pid, temp, nginx_root, concurrency):
    rows = {}
    baseline = snapshot([nginx_pid])
    for mib in (256, 512):
        peer.size = mib * 1024 * 1024
        stop = threading.Event()
        peak = {**baseline, "disk_bytes": 0}

        def sample(stop=stop, peak=peak):
            while not stop.is_set():
                # Native is short lived. Include every native/curl/strace child
                # of the launcher, plus the nginx master and workers.
                pids = [nginx_pid]
                for task in Path(f"/proc/{os.getpid()}/task").glob("*/children"):
                    try:
                        for pid in map(int, task.read_text().split()):
                            comm = Path(f"/proc/{pid}/comm").read_text().strip()
                            if comm in {"strace", "dawnop-native", "curl"}:
                                pids.append(pid)
                    except OSError:
                        pass
                current = snapshot(pids)
                for key in current:
                    peak[key] = max(peak[key], current[key])
                disk = temporary_bytes(
                    (temp, nginx_root / "proxy-temp", nginx_root / "client-temp"),
                    nginx_pid,
                )
                peak["disk_bytes"] = max(peak["disk_bytes"], disk)
                stop.wait(0.01)

        thread = threading.Thread(target=sample)
        thread.start()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                values = list(
                    pool.map(
                        lambda _: fetch(number, token, slow=True), range(concurrency)
                    )
                )
            check(
                all(
                    r["complete"]
                    and r["bytes"] == peer.size
                    and r["sha256"] == expected_digest(peer.size)
                    for r in values
                ),
                "large complete digest",
            )
        finally:
            stop.set()
            thread.join()
        check(
            peak["disk_bytes"] <= GATES["disk_bytes_per_request"] * concurrency,
            "no body staging",
        )
        rows[str(mib)] = {
            **peak,
            "sha256": values[0]["sha256"],
            "concurrency": concurrency,
        }
    growth = (rows["512"]["pss_kib"] - rows["256"]["pss_kib"]) / concurrency
    check(
        growth <= GATES["memory_growth_kib_per_request"],
        "bounded transport memory growth",
    )
    return {
        "baseline_nginx": baseline,
        "sizes_mib": rows,
        "pss_growth_kib_per_request": growth,
        "source_process": snapshot([os.getpid()], descendants=False),
        "kernel_sockstat": Path("/proc/net/sockstat").read_text().splitlines(),
        "cgroup_scope": "shared host cgroup, not isolated per-request accounting",
        "cgroup_available": {
            name: (Path("/sys/fs/cgroup") / name).read_text().strip()
            for name in (
                "memory.current",
                "memory.events",
                "memory.stat",
                "memory.pressure",
                "io.stat",
                "io.pressure",
            )
            if (Path("/sys/fs/cgroup") / name).is_file()
        },
    }


def negative_controls(
    root, config, number, token, peer, application_port, plain, results
):
    controls = {}
    variants = {
        "remove-internal": config.replace("      internal;", ""),
        "forward-authorization": config.replace(
            "proxy_pass_request_headers off;",
            "proxy_pass_request_headers off;\n      proxy_set_header Authorization $http_authorization;",
        ),
        "drop-range": config.replace(
            "proxy_set_header Range $http_range;", 'proxy_set_header Range "";'
        ),
        "mixed-validators": config.replace(
            "proxy_hide_header Content-Type;",
            "proxy_hide_header Content-Type;\n      proxy_hide_header ETag;",
        ).replace(
            "add_header Content-Type $download_response_type always;",
            "add_header Content-Type $download_response_type always;\n      add_header ETag '\"old-key\"' always;",
        ),
        "401-as-file": config.replace("400 401 402", "400 402")
        .replace(
            "error_page 504", "error_page 401 =200 @bad_file;\n      error_page 504"
        )
        .replace(
            "    location @download_failure",
            '    location @bad_file { default_type application/octet-stream; return 200 \'{"upstream":"rejected"}\'; }\n    location @download_failure',
        ),
        "ignore-cancellation": config.replace(
            "proxy_ignore_client_abort off;", "proxy_ignore_client_abort on;"
        ).replace("proxy_read_timeout 1s;", "proxy_read_timeout 5s;"),
        "buffer-to-disk": config.replace(
            "proxy_buffering off;", "proxy_buffering on;"
        ).replace(
            "proxy_max_temp_file_size 0;",
            "proxy_max_temp_file_size 1024m;\n      proxy_buffers 8 4k;",
        ),
    }
    for name, variant in variants.items():
        directory = root / name
        directory.mkdir()
        if name == "ignore-cancellation":
            # Hold read timeout at 5s in BOTH controls so it cannot mask a
            # broken cancellation behind the 2s release gate.
            with nginx(
                directory,
                variant.replace(
                    "proxy_ignore_client_abort on;", "proxy_ignore_client_abort off;"
                ),
            ) as p:
                wait_port(number, p)
                cancel(number, token, peer, before_headers=True)
        # Each config owns the same isolated prefix sequentially, no reload.
        with nginx(directory, variant) as p:
            wait_port(number, p)
            if name == "remove-internal":
                # A direct external request now enters object handling (502),
                # violating the required 404 even without a target.
                r = fetch(number, token, path="/_dawn_object")
                red = r["status"] != 404
            elif name == "forward-authorization":
                fetch(number, token)
                red = "Authorization" in peer.seen[-1]["headers"]
            elif name == "drop-range":
                peer.size = 65536
                r = fetch(number, token, headers={"Range": "bytes=1-2"})
                red = r["bytes"] != 2
            elif name == "mixed-validators":
                r = fetch(
                    number, token, headers={"Range": "bytes=1-2", "If-Range": ETAG}
                )
                red = header(r, "ETag") != ETAG
            elif name == "401-as-file":
                r = fetch(number, token, key="error-401")
                red = (
                    r["status"] == 200
                    and header(r, "Content-Type") != "application/json"
                )
            elif name == "ignore-cancellation":
                try:
                    cancel(number, token, peer, before_headers=True)
                    red = False
                except AssertionError:
                    red = True
                # Close this nginx before proceeding; do not complete 512 MiB.
            else:
                peer.size = 32 * 1024 * 1024
                c = http.client.HTTPConnection("127.0.0.1", number, timeout=5)
                c.connect()
                c.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                c.request(
                    "GET",
                    "/api/fm/content?path=payload",
                    headers={"Authorization": "Bearer " + token},
                )
                r = c.getresponse()
                deadline = time.monotonic() + 3
                disk = 0
                while time.monotonic() < deadline:
                    disk = temporary_bytes((root / "positive/proxy-temp",), p.pid)
                    if disk > 1048576:
                        break
                    time.sleep(0.02)
                red = disk > 1048576
                controls["buffer_actual_disk_bytes"] = disk
                r.close()
                c.close()
            check(red, "negative control failed to turn red: " + name)
            controls[name] = "RED"
            print("RED " + name, flush=True)
        peer.delay = 0
    legacy = (
        config.replace(
            "proxy_set_header If-Match $http_if_match;", 'proxy_set_header If-Match "";'
        )
        .replace(
            "proxy_hide_header Content-Type;",
            "proxy_hide_header Content-Type;\n      proxy_hide_header ETag;",
        )
        .replace(
            "add_header Content-Type $download_response_type always;",
            "add_header Content-Type $download_response_type always;\n      add_header ETag '\"old-key\"' always;",
        )
    )
    with nginx(root / "legacy-filter", legacy) as p:
        wait_port(number, p)
        r = fetch(number, token, headers={"If-Match": '"old-key"'})
        check(r["status"] in {200, 412}, "legacy workaround actual response")
        check(
            "If-Match" not in peer.seen[-1]["headers"],
            "legacy outbound header really cleared",
        )
        controls["legacy-native-clear-add-header-counterexample"] = {
            "expected_if_old_tag_worked": 200,
            "actual": r["status"],
        }
    peer.mode = "cacheable"
    peer.size = 65536
    cacheable = legacy.replace(
        "http {",
        "http {\n  proxy_cache_path "
        + str(root / "legacy-cache")
        + " keys_zone=legacy:1m max_size=1m;",
    ).replace("proxy_cache off;", "proxy_cache legacy; proxy_cache_valid 200 60s;")
    with nginx(root / "legacy-cache-filter", cacheable) as p:
        wait_port(number, p)
        r = fetch(number, token, headers={"If-Match": '"old-key"'})
        check(
            r["status"] == 412,
            "cacheable proxy exposes original condition despite outgoing clear and late ETag",
        )
        controls["legacy-native-clear-add-header-counterexample"][
            "cacheable_actual"
        ] = r["status"]
    peer.mode = "normal"
    # Real old stage code is the no-handoff route, not a fake delayed response.
    plain.size = 8 * 1024 * 1024
    plain.delay = 0.0125
    r = fetch(application_port, token)
    check(r["body_ms"] > 1000, "restored stage negative control")
    controls["restore-whole-stage"] = "RED"
    plain.delay = 0
    results["negative_controls"] = controls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path)
    parser.add_argument(
        "--binary", type=Path, help="reuse an explicitly built candidate ELF"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="ten alternating rounds and 256/512 MiB, single and four slow clients",
    )
    args = parser.parse_args()
    work = args.work or Path(tempfile.mkdtemp(prefix="site-download-k1-"))
    work.mkdir(parents=True, exist_ok=True)
    check(not any(work.iterdir()), "empty work directory")
    results = {
        "gates": GATES,
        "gaps": [],
        "versions": {
            "nginx": command(["nginx", "-v"]).stderr.decode().strip(),
            "python": sys.version.split()[0],
            "compiler": (ROOT / ".dawn-version").read_text().strip(),
        },
        "production": "not deployed",
    }
    runtime = work / "private"
    runtime.mkdir(mode=0o700)
    peers, servers, jvm = [], [], None
    try:
        dawnc = command([ROOT / "scripts/fetch-dawn-native.sh"]).stdout.decode().strip()
        binary = runtime / "dawnop-native"
        if args.binary:
            shutil.copy2(args.binary, binary)
        else:
            command([dawnc, "build", ROOT / "backend-dawn/app/native", "-o", binary])
        if not args.binary:
            command([dawnc, "test", ROOT / "backend-dawn/app/native"])
        cert, key = runtime / "cert.pem", runtime / "key.pem"
        command(
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
                "-addext",
                "subjectAltName=DNS:localhost,DNS:objects.invalid",
                "-keyout",
                key,
                "-out",
                cert,
            ]
        )
        tls = Peer(contract_qiniu_fake.FAKE_AK, contract_qiniu_fake.FAKE_SK, cert, key)
        plain = tls
        peers = [tls]
        tls.origin = f"https://localhost:{tls.server_port}"
        truststore = runtime / "truststore"
        command(
            [
                "keytool",
                "-importcert",
                "-noprompt",
                "-alias",
                "isolated",
                "-file",
                cert,
                "-keystore",
                truststore,
                "-storepass",
                "changeit",
            ]
        )

        db = runtime / "fixture.db"
        contract_fixture.build(str(db))
        seed(
            db,
            [
                "payload",
                "dirty",
                "controls",
                "中文 space%ZZ.txt",
                "../x",
                "a/../b",
                "a\\b",
                "x\r\ny",
                "a//b",
                "short",
                "stall-body",
                "stall-head",
                "cancel-head",
                "drip",
                "ignore-range",
                *[
                    f"error-{n}"
                    for n in (
                        200,
                        300,
                        301,
                        302,
                        307,
                        308,
                        400,
                        401,
                        403,
                        404,
                        405,
                        408,
                        429,
                        500,
                        502,
                        503,
                        504,
                        599,
                        612,
                    )
                ],
            ],
        )
        temp = runtime / "app-temp"
        temp.mkdir()
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("DAWN_", "QINIU_", "TENCENT_"))
            and k not in {"SECRET_KEY", "DAWNOP_ENV", "LIGHTHOUSE_INSTANCE_ID"}
        }
        env.update(
            TMPDIR=str(temp),
            TZ="UTC",
            NO_PROXY="*",
            no_proxy="*",
            CURL_CA_BUNDLE=str(cert),
            JAVA_TOOL_OPTIONS="-Djavax.net.ssl.trustStore="
            + str(truststore)
            + " -Djavax.net.ssl.trustStorePassword=changeit",
        )
        for arm in ("candidate", "stage"):
            envf = runtime / (arm + ".env")
            contract_run.env_file(
                envf, db, 1, tls.origin if arm == "candidate" else plain.origin
            )
            if arm == "candidate":
                with envf.open("a") as stream:
                    stream.write(
                        "\nDAWN_DOWNLOAD_HANDOFF=on\nDAWN_DOWNLOAD_ORIGIN="
                        + tls.origin
                        + "\n"
                    )
            server = contract_native.NativeServer(
                binary, {**env, "DAWNOP_ENV": str(envf)}, 0, runtime / arm
            )
            if arm == "candidate":
                server.execute("--migrate")
            server.start()
            servers.append(server)
        candidate, stage = servers
        token = login(candidate.port)
        login(stage.port)
        number = port()
        jvm_port = port()
        positive = runtime / "positive"
        positive.mkdir()
        config = render(
            positive,
            number,
            candidate.port,
            tls.origin,
            f"127.0.0.1:{tls.server_port}",
            cert,
        )
        front_locations = ""
        for name, app_port in (("stage", stage.port), ("jvm", jvm_port)):
            front_locations += f"location = /_{name}_content {{ proxy_pass http://127.0.0.1:{app_port}/api/fm/content; proxy_http_version 1.1; proxy_set_header Connection close; proxy_buffering off; proxy_next_upstream off; }}\n"
        config = config.replace(
            "location / { return 404; }", front_locations + "location / { return 404; }"
        )
        # Prove the nonstandard status limit using the actual nginx validator.
        invalid = runtime / "invalid.conf"
        invalid.write_text(config.replace("error_page 504", "error_page 612"))
        rejected = subprocess.run(
            [
                "nginx",
                "-e",
                "/dev/null",
                "-t",
                "-p",
                str(positive) + "/",
                "-c",
                str(invalid),
            ],
            capture_output=True,
        )
        check(
            rejected.returncode != 0
            and b'value "612" must be between 300 and 599' in rejected.stderr,
            "612 stock configuration gap",
        )
        tls_proofs = {}
        for label, variant in {
            "wrong-sni": config.replace(
                "proxy_ssl_name localhost;", "proxy_ssl_name wrong.invalid;"
            ),
            "untrusted-certificate": config.replace(
                str(cert), str(runtime / "untrusted.pem")
            ),
        }.items():
            if label == "untrusted-certificate":
                command(
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
                        "/CN=untrusted.invalid",
                        "-keyout",
                        runtime / "untrusted-key.pem",
                        "-out",
                        runtime / "untrusted.pem",
                    ]
                )
            before = len(tls.seen)
            with nginx(runtime / label, variant) as p:
                wait_port(number, p)
                r = fetch(number, token)
                check(
                    r["status"] == 502 and len(tls.seen) == before,
                    label + " fails before object HTTP",
                )
                tls_proofs[label] = "502, zero object HTTP requests"
        results["TLS"] = tls_proofs
        results["connect_timeout"] = handshake_timeout(
            runtime, config, number, token, tls
        )
        candidate_env = runtime / "candidate.env"
        configured = candidate_env.read_text()
        for invalid_origin in (
            "",
            "https://wrong.invalid",
            "http://objects.invalid",
            tls.origin + "/path",
            "https://user@objects.invalid",
        ):
            candidate.quiesce()
            candidate_env.write_text(
                configured.replace(
                    "DAWN_DOWNLOAD_ORIGIN=" + tls.origin,
                    "DAWN_DOWNLOAD_ORIGIN=" + invalid_origin,
                )
            )
            before = len(tls.seen)
            r = fetch(candidate.port, token)
            check(
                r["status"] == 502 and len(tls.seen) == before,
                "invalid configured authority fails closed",
            )
        candidate.quiesce()
        candidate_env.write_text(configured)
        before = len(plain.seen)
        r = fetch(
            stage.port,
            token,
            headers={
                "DAWN_DOWNLOAD_HANDOFF": "on",
                "X-Accel-Redirect": "/_dawn_object",
            },
        )
        check(
            r["status"] == 200
            and header(r, "X-Accel-Redirect") is None
            and len(plain.seen) == before + 1,
            "default off ignores forged feature controls",
        )
        results["configuration"] = {
            "invalid_origins_rejected": 5,
            "default_off": "old real stage behavior",
        }
        negative_controls(
            runtime, config, number, token, tls, stage.port, plain, results
        )
        with nginx(positive, config) as p:
            wait_port(number, p)
            tls.size = 65536
            protocol(number, token, tls, db, results)
            results["cancellation"] = {
                "body": cancel(number, token, tls),
                "before_headers": cancel(number, token, tls, before_headers=True),
            }
            results["downstream_idle_and_limit"] = idle_and_limit(number, token, tls)
            if args.full:
                envf = runtime / "jvm.env"
                contract_run.env_file(envf, db, jvm_port, plain.origin)
                log = (runtime / "jvm.log").open("wb")
                jvm = subprocess.Popen(
                    ["java", "-jar", str(ROOT / "backend-dawn/backend-dawn.jar")],
                    env={**env, "DAWNOP_ENV": str(envf)},
                    stdout=log,
                    stderr=log,
                )
                wait_port(jvm_port, jvm)
                rows = {name: [] for name in ("candidate", "stage", "jvm")}
                tls.size = plain.size = 8 * 1024 * 1024
                tls.delay = plain.delay = 0.0125
                groups = {}
                for group in ("cold", "warm"):
                    rows = {name: [] for name in ("candidate", "stage", "jvm")}
                    for iteration in range(GATES["rounds"]):
                        if group == "cold" and iteration > 0:
                            jvm.terminate()
                            jvm.wait(timeout=5)
                            jvm = subprocess.Popen(
                                [
                                    "java",
                                    "-jar",
                                    str(ROOT / "backend-dawn/backend-dawn.jar"),
                                ],
                                env={**env, "DAWNOP_ENV": str(envf)},
                                stdout=log,
                                stderr=log,
                            )
                            wait_port(jvm_port, jvm)
                        for name in (
                            list(rows)[iteration % 3 :] + list(rows)[: iteration % 3]
                        ):
                            r = fetch(
                                number,
                                token,
                                path=(
                                    "/api/fm/content?path=payload"
                                    if name == "candidate"
                                    else f"/_{name}_content?path=payload"
                                ),
                            )
                            check(
                                r["complete"]
                                and r["sha256"] == expected_digest(tls.size),
                                "performance complete digest",
                            )
                            rows[name].append(
                                {k: r[k] for k in ("head_ms", "body_ms", "total_ms")}
                            )
                    quantile = {
                        name: quantiles(values) for name, values in rows.items()
                    }
                    candidate_p95 = quantile["candidate"]["body_ms"]["p95"]
                    check(
                        candidate_p95
                        <= max(250, quantile["jvm"]["body_ms"]["p95"] + 100),
                        group + " candidate body TTFB absolute/JVM gate",
                    )
                    check(
                        candidate_p95 <= quantile["stage"]["body_ms"]["p95"] / 4,
                        group + " candidate body TTFB 4x stage gate",
                    )
                    groups[group] = {"quantiles": quantile, "rounds": rows}
                    print("PASS " + group + " performance gates", flush=True)
                results["performance"] = {
                    **groups,
                    "all_arms": "same client/nginx/TLS fake object, same bytes and source delay",
                    "cold_scope": "new JVM for each cold request; native is new for every request in both groups; nginx stays resident and TLS sessions are not reused; no host page-cache flush",
                }
                tls.delay = plain.delay = 0
                results["resources"] = {
                    str(n): resources(number, token, tls, p.pid, temp, positive, n)
                    for n in (1, 4)
                }
        traces = list((runtime / "candidate").glob("exec-*.log"))
        check(
            not any('/curl"' in f.read_text() for f in traces),
            "candidate must never exec curl",
        )
        check(
            not any(item.get("bad_signature") for item in tls.seen),
            "all candidate signatures verified",
        )
        results["production_gate"] = (
            "BLOCKED: concrete error contract gaps and pending DAV integration"
        )
        results["source_requests"] = len(tls.seen)
        results["candidate_curl_execs"] = 0
        results["passed"] = True
    finally:
        if jvm:
            jvm.terminate()
            jvm.wait(timeout=5)
        for server in servers:
            try:
                server.quiesce()
                server.close()
            except AssertionError as error:
                results.setdefault("cleanup_errors", []).append(str(error))
        for peer in peers:
            peer.close()
        shutil.rmtree(runtime)
        (work / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    check(not results.get("cleanup_errors"), "native exchange audit and cleanup")
    print(
        json.dumps(
            {
                "passed": results.get("passed", False),
                "protocol": results["protocol"]["passed"],
                "negative_controls": results["negative_controls"],
                "gaps": results["gaps"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
