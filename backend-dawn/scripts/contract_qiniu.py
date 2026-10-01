#!/usr/bin/env python3
"""Object-store contract: the paths that reach qiniu, against a fake bucket.

The other three scripts run with QINIU_* empty and name every object-store case
a skip. That left the interesting half of fm and WebDAV outside CI — including
COPY of a *subtree*, the one place the backend issues one qiniu call per file
row, and the one place a failure has to become a 502 rather than a 500 (it was a
500 once; see errkind.dawn's header comment).

This script runs against a *second* backend, configured to talk to
contract_qiniu_fake.py on loopback instead of rs.qiniu.com / upload.qiniup.com
(QINIU_RS_HOST / QINIU_UP_HOST / QINIU_DOMAIN). Everything above the HTTP call
is the production code path — the signing, the request shaping, the row
bookkeeping, the status mapping. Its golden is therefore recorded under a
DIFFERENT environment fingerprint (`qiniu_configured: true`), so these bytes can
never be confused with the credential-free ones.

What a fake can and cannot buy:

  covered      the qiniu call *sequence* (which keys, how many, in what order —
               read back from the fake's log, so "copied the subtree" means the
               objects moved, not just that rows appeared), signature
               correctness (the fake recomputes every HMAC and rejects a bad
               one), the byte round trip PUT -> GET, the write-new-key-then-drop
               -the-old invariant on overwrite/save, and the refusal mapping
               (qiniu says no -> 502, with the upstream status in the message).

  NOT covered  qiniu's own behaviour: regions, the full error taxonomy, CDN
               caching, multipart resume, real quota stops. A golden could not
               have pinned those against a live bucket either.

Normally driven by contract_run.py. Standalone (start the fake first):

    TOKEN=... DAV_USER=... DAV_PASS=... CONTRACT_DB_PATH=... \\
        FAKE_QINIU=http://127.0.0.1:18100 \\
        python3 contract_qiniu.py --base http://127.0.0.1:18002
"""

import argparse
import base64
import hashlib
import json
import os
import re
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import contract_qiniu_fake
from contract_golden import TRANSPORT_STATUS, Golden, refused_unread, transport_error
from contract_webdav import KEEP_HEADERS, PREFIX, facts, raw_http, wire_report
from contract_webdav import normalize as dav_normalize

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hand the 302 back instead of following it.

    GET has two branches keyed on the User-Agent — proxy the bytes, or 302 the
    client at a signed URL — and only one of them can be observed with an opener
    that follows redirects. Both are contract.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


NOFOLLOW = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)

# Location is not in contract_webdav's list (nothing there redirects); here the
# signed redirect URL is the point of a case.
KEEP = (*KEEP_HEADERS, "location")

# Object keys are freshly minted uuids (32 hex, sometimes with an extension), so
# they differ every run and every one of them has to be normalized — in bodies,
# in ETags, in the fake's call log. \b keeps this off the 40-hex sha1 the fake
# reports as an object hash.
#
# They are numbered in order of first appearance rather than all flattened to
# one placeholder, because the *distinctness* of keys is contract: an overwrite
# that reused the key, or a subtree copy that pointed two rows at one object,
# would otherwise record identically to the correct behaviour.
_KEY = re.compile(r"\b[0-9a-f]{32}\b")
_ALIAS = {}


def _alias(m) -> str:
    return _ALIAS.setdefault(m.group(0), f"<key{len(_ALIAS) + 1}>")


# a signed download URL: ?e=<deadline>&token=<ak>:<hmac> — both move every run
_SIGNED = re.compile(r"e=\d+&token=[^&\s\"<]+")

SANDBOX = f"qiniu://{PREFIX}"
# A client the backend will NOT 302 (see webdav.REDIRECT_CLIENTS): it proxies the
# bytes instead, which is the branch macOS webdavfs and the Windows redirector
# take. urllib's default UA already qualifies; naming it keeps the golden honest
# about which branch produced these bytes.
PROXY_UA = "contract-proxy-client/1.0"
REDIRECT_UA = "rclone/v1.65.0"

# The fake's base URL, filled in by main(). It carries whichever port the harness
# picked, so it is scrubbed out of anything recorded. A one-element list so
# scrub() can read it without a `global`.
FAKE_BASE = [""]


def dav(base, method, path, auth, headers=None, body=None, opener=OPENER, timeout=30):
    """A WebDAV request with Basic auth. Local rather than contract_webdav's so
    the opener can be swapped for the non-following one."""
    hdrs = dict(headers or {})
    hdrs["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
    data = body if isinstance(body, (bytes, type(None))) else body.encode()
    r = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
    try:
        with opener.open(r, timeout=timeout) as resp:
            return (
                resp.status,
                {k.lower(): v for k, v in resp.headers.items()},
                resp.read(),
            )
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read()
    except Exception as e:  # noqa: BLE001 - transport failure is a case failure
        return TRANSPORT_STATUS, {}, transport_error(e).encode()


def _fresh_key(value):
    """Replace any freshly minted object key with one fixed placeholder.

    scrub() numbers keys in order of first appearance across the whole run, which
    makes every case that records one depend on how many were minted before it. A
    case that is not about key distinctness should not carry that coupling.
    """
    if isinstance(value, dict):
        return {k: _fresh_key(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_fresh_key(v) for v in value]
    if isinstance(value, str):
        return _KEY.sub("<fresh-key>", value)
    return value


def scrub(value):
    """Replace the per-run identifiers in a recorded value.

    Timestamps on rows this run created are wall clock (SQLite CURRENT_TIMESTAMP)
    and are replaced only for entries under the sandbox — a fixture row's
    timestamp staying pinned is the point of the distinction. The fake's base URL
    goes too: it carries the port the harness happened to pick.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "token" and isinstance(v, str):
                out[k] = "<upload-token>"
            elif k == "last_modified" and str(value.get("path", "")).startswith(
                SANDBOX
            ):
                out[k] = "WALL-CLOCK"
            else:
                out[k] = scrub(v)
        return out
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        out = _SIGNED.sub("e=<deadline>&token=<signature>", _KEY.sub(_alias, value))
        return out.replace(FAKE_BASE[0], "<fake-qiniu>") if FAKE_BASE[0] else out
    return value


def api(base, method, path, token=None, body=None, headers=None, timeout=30):
    hdrs = {k: v for k, v in (headers or {}).items()}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
    try:
        with OPENER.open(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001 - transport failure is a case failure
        return TRANSPORT_STATUS, transport_error(e).encode()


def put_policy(upload_token: str) -> dict:
    """The putPolicy carried in `<ak>:<sign>:<base64url policy>`, or {}."""
    parts = upload_token.split(":")
    if len(parts) != 3:
        return {}
    encoded = parts[2]
    pad = "=" * (-len(encoded) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(encoded + pad))
    except (ValueError, TypeError):
        return {}


def spend_upload_token(fake_base, upload_token, key, content):
    """Upload straight to the bucket with a minted token, as the browser does.

    /upload-token exists so the bytes never touch this backend, so nothing on the
    backend's own paths can tell whether the token it minted is usable. Only
    spending it can.
    """
    boundary = contract_qiniu_fake.BOUNDARY
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
        f"\r\n\r\n{value}\r\n".encode()
        for name, value in (("token", upload_token), ("key", key))
    ]
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{key}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
    )
    parts.append(content + f"\r\n--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        fake_base + "/",
        data=b"".join(parts),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with OPENER.open(request, timeout=10) as response:
            return response.status, body_value(response.read())
    except urllib.error.HTTPError as e:
        return e.code, body_value(e.read())
    except Exception as e:  # noqa: BLE001 - transport failure is a case failure
        return TRANSPORT_STATUS, transport_error(e)


# The backend's half of a timeout message is ours to pin; the JDK's half is not.
# `outbound HTTP timed out after 10s: ` is written by util/http and is the part
# that carries meaning (which budget expired); what follows is whatever
# java.net.http's exception says on the JDK of the day, and pinning that would
# make this golden a report on the runtime rather than on this backend.
_JDK_TIMEOUT_TEXT = re.compile(r"(timed out after \d+s: ).*")


def normalize_timeout(value):
    if isinstance(value, dict):
        return {k: normalize_timeout(v) for k, v in value.items()}
    if isinstance(value, str):
        return _JDK_TIMEOUT_TEXT.sub(r"\1<jdk timeout text>", value)
    return value


def body_value(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")


def file_metadata(db_path, path):
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            'SELECT path, is_dir, "key", content_type, size FROM files WHERE path = ?',
            (path,),
        ).fetchone()
    if row is None:
        return None
    return {
        "path": row[0],
        "is_dir": bool(row[1]),
        "key": row[2],
        "content_type": row[3],
        "size": row[4],
    }


class Fake:
    """The fake bucket's control plane."""

    def __init__(self, base: str):
        self.base = base

    def _post(self, path, payload=None):
        data = json.dumps(payload or {}).encode()
        r = urllib.request.Request(
            self.base + path,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with OPENER.open(r, timeout=10) as resp:
            return json.loads(resp.read())

    def _get(self, path):
        with OPENER.open(self.base + path, timeout=10) as resp:
            return json.loads(resp.read())

    def reset(self):
        self._post("/__fake/reset")

    def clear_calls(self):
        self._post("/__fake/calls")

    def refuse(self, op, status, body='{"error":"contract fake refuses"}'):
        self._post("/__fake/refuse", {"op": op, "status": status, "body": body})

    def allow(self, op):
        self._post("/__fake/refuse", {"op": op, "status": 0})

    def plant(self, key, content, mime):
        self._post("/__fake/put", {"key": key, "content": content, "mime": mime})

    def pause(self, op):
        self._post("/__fake/pause", {"op": op})

    def stall(self, op, seconds):
        self._post("/__fake/stall", {"op": op, "seconds": seconds})

    def unstall(self, op):
        self._post("/__fake/stall", {"op": op, "seconds": 0})

    def release(self, op):
        self._post("/__fake/release", {"op": op})

    def pauses(self):
        return self._get("/__fake/pauses")["pauses"]

    def calls(self):
        return self._get("/__fake/calls")["calls"]

    def keys(self):
        return self._get("/__fake/objects")["keys"]

    def state(self):
        return self._get("/__fake/state")["objects"]


# --------------------------------------------------------------------------- #
# Upload links ("drops"): /api/fm/drops* for the admin, /api/drop* for the
# anonymous uploader. Everything a drop token may and may not do is pinned here,
# against the fake bucket, because the interesting half (spending the direct
# credential, the stat in register, the object deleted after a refusal) needs an
# object store to be observable at all.

DROP_HEADER = "X-Drop-Token"


def drop_req(base, method, path, drop_token=None, body=None, headers=None, raw=None):
    """A request to the drop surface. `drop_token` goes in X-Drop-Token and
    nowhere else; `raw` sends bytes as the body instead of JSON."""
    hdrs = dict(headers or {})
    if drop_token is not None:
        hdrs[DROP_HEADER] = drop_token
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
    try:
        with OPENER.open(r, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001 - transport failure is a case failure
        return TRANSPORT_STATUS, transport_error(e).encode()


def raw_request(base, method, path, header_pairs, body=b"", timeout=15):
    """Exactly these header lines and this body over a socket; (status, body).

    urllib always frames a body with a Content-Length it computes itself, so a
    chunked upload, or one header sent twice, can only be spelled here.
    """
    parts = urllib.parse.urlsplit(base)
    host, port = parts.hostname, parts.port or 80
    lines = [
        f"{method} {path} HTTP/1.1",
        f"Host: {host}:{port}",
        "Connection: close",
        *(f"{k}: {v}" for k, v in header_pairs),
    ]
    request = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(request)
        buf = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("latin-1")
    try:
        status = int(status_line.split(" ")[1])
    except (IndexError, ValueError):
        status = TRANSPORT_STATUS
    return status, rest


def policy_facts(upload_token, key, expect_limit):
    """What the direct credential lets its holder do, read off its putPolicy."""
    policy = put_policy(upload_token)
    deadline = policy.get("deadline", 0)
    remaining = deadline - int(time.time())
    return {
        "scope_is_bucket_key": policy.get("scope")
        == f"{contract_qiniu_fake.FAKE_BUCKET}:{key}",
        "insert_only": policy.get("insertOnly"),
        "fsize_limit": policy.get("fsizeLimit"),
        "fsize_limit_expected": policy.get("fsizeLimit") == expect_limit,
        "deadline_within_an_hour": 0 < remaining <= 3600,
        "fields": sorted(policy),
    }


def drop_cases(g, B, token, auth, fake, fake_base, db_path):  # noqa: C901 - a case list
    inbox_rel = f"{PREFIX}/drop-inbox"
    inbox = f"qiniu://{inbox_rel}"
    public_bodies = []  # every uploader-facing answer except upload-token's
    minted_keys = []

    def admin(method, path, body=None, auth_token=token):
        st, raw = api(B, method, path, auth_token, body)
        return st, body_value(raw)

    def public(method, path, drop_token=None, body=None, headers=None, raw=None):
        st, out = drop_req(B, method, path, drop_token, body, headers, raw)
        if not path.startswith("/api/drop/upload-token"):
            public_bodies.append(out)
        return st, body_value(out)

    def create(**fields):
        body = {"dir": inbox, **fields}
        st, out = admin("POST", "/api/fm/drops", body)
        return st, out, (out.get("token", "") if isinstance(out, dict) else "")

    def shown(created):
        """A create/list item with the per-run values replaced by facts."""
        if not isinstance(created, dict):
            return created
        out = dict(created)
        if "token" in out:
            out["token"] = f"<{len(out['token'])}-char token>"
        if "created_at" in out:
            out["created_at"] = (
                "WALL-CLOCK"
                if re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", out["created_at"])
                else out["created_at"]
            )
        if "expires_at" in out:
            out["expires_at"] = "WALL-CLOCK"
        return out

    def expire(drop_id):
        with sqlite3.connect(db_path) as con:
            con.execute(
                "UPDATE upload_drops SET expires_at = ? WHERE id = ?",
                (int(time.time()) - 1, drop_id),
            )

    def grant(drop_token, name, size):
        st, out = public(
            "POST", "/api/drop/upload-token", drop_token, {"name": name, "size": size}
        )
        if st == 200:
            minted_keys.append(out["key"])
        return st, out

    def spend(granted, content):
        st, out = spend_upload_token(
            fake_base, granted.get("token", ""), granted.get("key", ""), content
        )
        # a list, not a tuple: the golden stores JSON, and a tuple reads back
        # as a list
        return [st, _fresh_key(out)]

    def file_row(name):
        return file_metadata(db_path, f"{inbox_rel}/{name}")

    print("\n== drops: setup ==")
    st, _ = admin(
        "POST", "/api/fm/create-folder", {"path": SANDBOX, "name": "drop-inbox"}
    )
    g.case("drop.setup.inbox", {"status": st})

    print("\n== drops: the admin door is the admin JWT ==")
    statuses = {}
    for method, path, body in (
        ("POST", "/api/fm/drops", {"dir": inbox}),
        ("GET", "/api/fm/drops", None),
        ("POST", "/api/fm/drops/revoke", {"id": 1}),
        ("POST", "/api/fm/drops/delete", {"id": 1}),
    ):
        st, out = admin(method, path, body, auth_token=None)
        statuses[f"{method} {path}"] = {"status": st, "body": out}
    g.case("drop.admin.requires-jwt", statuses)

    print("\n== drops: create ==")
    st, made, t_main = create(label="合同 inbox")
    main_id = made.get("id") if isinstance(made, dict) else None
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT token_hash, expires_at FROM upload_drops WHERE id = ?", (main_id,)
        ).fetchone()
        dump = "\n".join(con.iterdump())
    g.case(
        "drop.create.defaults",
        {
            "status": st,
            "body": shown(made),
            "expires_in_about_a_day": row is not None
            and abs(row[1] - int(time.time()) - 86400) <= 5,
            "stored_hash_is_sha256_of_token": row is not None
            and row[0] == hashlib.sha256(t_main.encode()).hexdigest(),
            "plaintext_token_not_in_database": t_main != "" and t_main not in dump,
        },
    )

    big = "x" * 101
    invalid = {}
    for label, body in (
        ("expires_in_s.below", {"dir": inbox, "expires_in_s": 59}),
        ("expires_in_s.above", {"dir": inbox, "expires_in_s": 2592001}),
        ("max_files.zero", {"dir": inbox, "max_files": 0}),
        ("max_files.above", {"dir": inbox, "max_files": 1001}),
        ("max_files.string", {"dir": inbox, "max_files": "20"}),
        ("max_files.float", {"dir": inbox, "max_files": 1.5}),
        ("max_file_bytes.above", {"dir": inbox, "max_file_bytes": 5368709121}),
        ("max_total_bytes.above", {"dir": inbox, "max_total_bytes": 53687091201}),
        ("label.too-long", {"dir": inbox, "label": big}),
        ("dir.absent-field", {"label": "x"}),
        ("dir.missing", {"dir": f"{SANDBOX}/no-such-dir"}),
        ("dir.is-a-file", {"dir": "qiniu://example.txt"}),
    ):
        st, out = admin("POST", "/api/fm/drops", body)
        invalid[label] = {"status": st, "body": out}
    g.case("drop.create.refusals", invalid)

    print("\n== drops: the uploader door reads X-Drop-Token and nothing else ==")
    st, out = public("GET", "/api/drop", t_main)
    g.case("drop.info", {"status": st, "body": shown(out)})

    placements = {}
    for label, kwargs in (
        ("no-header", {}),
        ("query", {"path": f"/api/drop?token={t_main}"}),
        ("authorization-bearer", {"headers": {"Authorization": f"Bearer {t_main}"}}),
        ("authorization-bare", {"headers": {"Authorization": t_main}}),
        ("cookie", {"headers": {"Cookie": f"drop_token={t_main}; token={t_main}"}}),
        ("admin-jwt", {"headers": {"Authorization": f"Bearer {token}"}}),
        ("unknown-token", {"drop_token": "A" * 43}),
        ("empty-token", {"drop_token": ""}),
        ("oversized-token", {"drop_token": "A" * 300}),
    ):
        path = kwargs.pop("path", "/api/drop")
        st, out = public("GET", path, **kwargs)
        placements[label] = {"status": st, "body": out}
    st, raw = raw_request(
        B,
        "GET",
        "/api/drop",
        [(DROP_HEADER, t_main), (DROP_HEADER, t_main), ("Content-Length", "0")],
    )
    placements["header-twice"] = {"status": st, "body": body_value(raw)}
    g.case("drop.token.placement", placements)

    print("\n== drops: a drop token is not an admin credential ==")
    crossed = {}
    for label, path, headers in (
        (
            "fm.bearer",
            "/api/fm?path=qiniu%3A%2F%2F",
            {"Authorization": f"Bearer {t_main}"},
        ),
        ("fm.x-drop-token", "/api/fm?path=qiniu%3A%2F%2F", {DROP_HEADER: t_main}),
        (
            "fm.preview.query",
            f"/api/fm/preview?path=qiniu%3A%2F%2Fexample.txt&token={t_main}",
            {},
        ),
        (
            "fm.download.query",
            f"/api/fm/download?path=qiniu%3A%2F%2Fexample.txt&token={t_main}",
            {},
        ),
        ("fm.drops.bearer", "/api/fm/drops", {"Authorization": f"Bearer {t_main}"}),
        ("auth.me.bearer", "/api/auth/me", {"Authorization": f"Bearer {t_main}"}),
    ):
        st, out = api(B, "GET", path, None, None, headers)
        crossed[label] = {"status": st, "body": body_value(out)}
    for label, basic in (
        ("dav.basic.password", f"{auth.split(':', 1)[0]}:{t_main}"),
        ("dav.basic.user", f"{t_main}:"),
    ):
        st, _hd, _raw = dav(B, "PROPFIND", "/dav/", basic, {"Depth": "0"})
        crossed[label] = {"status": st}
    r = urllib.request.Request(
        B + "/dav/",
        method="PROPFIND",
        headers={"Authorization": f"Bearer {t_main}", "Depth": "0"},
    )
    try:
        with OPENER.open(r, timeout=30) as resp:
            crossed["dav.bearer"] = {"status": resp.status}
    except urllib.error.HTTPError as e:
        crossed["dav.bearer"] = {"status": e.code}
    g.case("drop.token.not-admin", crossed)

    print("\n== drops: a name is one segment ==")
    names = {}
    for label, name in (
        ("dotdot-slash", "../x"),
        ("slash", "a/b"),
        ("backslash", "a\\b"),
        ("dotdot", ".."),
        ("dot", "."),
        ("empty", ""),
        ("nul", "a\u0000b"),
        ("newline", "a\nb"),
        ("del", "a\u007f"),
        ("edge-space", " a.txt"),
        ("too-long", "n" * 256),
    ):
        st, out = grant(t_main, name, 1)
        names[label] = {"status": st, "body": out}
    st, out = public("POST", "/api/drop/upload-token", t_main, {"name": 1, "size": 1})
    names["not-a-string"] = {"status": st, "body": out}
    st, out = public("POST", "/api/drop/upload-token", t_main, {"name": "a.txt"})
    names["size-missing"] = {"status": st, "body": out}
    st, out = public(
        "POST", "/api/drop/upload-token", t_main, {"name": "a.txt", "size": -1}
    )
    names["size-negative"] = {"status": st, "body": out}
    for label, path in (
        ("put.encoded-slash", "/api/drop/files/a%2Fb"),
        ("put.dotdot", "/api/drop/files/.."),
        ("put.encoded-dotdot", "/api/drop/files/%2e%2e"),
    ):
        st, out = public("PUT", path, t_main, raw=b"x")
        names[label] = {"status": st, "body": out}
    g.case("drop.names", names)

    print("\n== drops: direct upload (upload-token -> bucket -> register) ==")
    hello = b"hello through a drop\n"
    st, granted = grant(t_main, "hello.txt", len(hello))
    g.case(
        "drop.direct.grant",
        {
            "status": st,
            "body": {
                **_fresh_key(granted),
                "token": "<upload-token>",
                "up_host": "<fake-qiniu>"
                if granted.get("up_host") == fake_base
                else granted.get("up_host"),
            },
            "policy": policy_facts(
                granted.get("token", ""), granted.get("key", ""), 104857600
            ),
        },
    )
    spent = spend(granted, hello)
    st, out = public("POST", "/api/drop/register", t_main, {"key": granted.get("key")})
    row = file_row("hello.txt")
    obj = fake.state().get(granted.get("key", ""), {})
    g.case(
        "drop.direct.register",
        {
            "spend": spent,
            "status": st,
            "body": out,
            "row": _fresh_key(row),
            "bytes_round_trip": base64.b64decode(obj.get("content", "")) == hello,
        },
    )
    st, out = public("POST", "/api/drop/register", t_main, {"key": granted.get("key")})
    g.case("drop.direct.register.replay", {"status": st, "body": out})
    # the credential cannot be spent twice either: insertOnly is in its policy,
    # so a second upload to the now-registered key cannot replace its bytes
    st, out = spend(granted, b"overwrite attempt\n")
    g.case(
        "drop.direct.insert-only",
        {
            "status": st,
            "body": out,
            "bytes_unchanged": base64.b64decode(
                fake.state().get(granted.get("key", ""), {}).get("content", "")
            )
            == hello,
        },
    )

    print("\n== drops: a taken name is renamed, never overwritten ==")
    before = file_row("hello.txt")
    before_bytes = fake.state().get((before or {}).get("key") or "", {}).get("content")
    st, again = grant(t_main, "hello.txt", 5)
    spent = spend(again, b"later")
    st2, out = public("POST", "/api/drop/register", t_main, {"key": again.get("key")})
    g.case(
        "drop.direct.rename",
        {
            "grant_status": st,
            "granted_name": again.get("name"),
            "spend": spent,
            "status": st2,
            "body": out,
            "original_row_unchanged": file_row("hello.txt") == before,
            "original_bytes_unchanged": fake.state()
            .get((before or {}).get("key") or "", {})
            .get("content")
            == before_bytes,
            "renamed_row": _fresh_key(file_row("hello (1).txt")),
        },
    )

    print("\n== drops: register accepts only this drop's own keys ==")
    st, other, t_other = create(label="other")
    other_id = other.get("id") if isinstance(other, dict) else None
    st, foreign = grant(t_other, "foreign.txt", 3)
    spend(foreign, b"abc")
    st, admin_minted = admin(
        "POST", "/api/fm/upload-token", {"path": inbox, "name": "admin.txt"}
    )
    admin_key = admin_minted.get("key", "") if isinstance(admin_minted, dict) else ""
    fake.plant(admin_key, "admin bytes", "text/plain")
    made_up = "0123456789abcdef0123456789abcdef.txt"
    fake.plant(made_up, "made up", "text/plain")
    refused = {}
    for label, key in (
        ("other-drops-key", foreign.get("key", "")),
        ("admin-signed-key", admin_key),
        ("made-up-key", made_up),
        ("empty-key", ""),
    ):
        st, out = public("POST", "/api/drop/register", t_main, {"key": key})
        refused[label] = {"status": st, "body": out}
    st, out = public("POST", "/api/drop/register", t_main, {})
    refused["key-missing"] = {"status": st, "body": out}
    # nothing above consumed anything: the other drop still registers its key
    st, out = public("POST", "/api/drop/register", t_other, {"key": foreign.get("key")})
    refused["owner-still-registers"] = {"status": st, "body": out}
    refused["no-row-for-foreign-keys"] = all(
        file_row(n) is None
        for n in ("admin.txt", "0123456789abcdef0123456789abcdef.txt")
    )
    g.case("drop.register.foreign-keys", refused)

    print("\n== drops: register before the object exists keeps the reservation ==")
    st, early = grant(t_main, "early.txt", 4)
    st1, out1 = public("POST", "/api/drop/register", t_main, {"key": early.get("key")})
    spend(early, b"late")
    st2, out2 = public("POST", "/api/drop/register", t_main, {"key": early.get("key")})
    g.case(
        "drop.register.not-uploaded-yet",
        {
            "before": {"status": st1, "body": out1},
            "after": {"status": st2, "body": out2},
        },
    )

    print("\n== drops: quota ==")
    st, q, t_q = create(max_files=2, max_file_bytes=100, max_total_bytes=150)
    q_id = q.get("id") if isinstance(q, dict) else None
    quota = {}
    st, out = grant(t_q, "too-big.bin", 101)
    quota["single-file-over"] = {"status": st, "body": out}
    st, first = grant(t_q, "first.bin", 100)
    quota["first.policy"] = policy_facts(
        first.get("token", ""), first.get("key", ""), 100
    )
    spend(first, b"f" * 100)
    st, out = public("POST", "/api/drop/register", t_q, {"key": first.get("key")})
    quota["first.register"] = {"status": st, "body": out}
    st, out = grant(t_q, "second.bin", 60)
    quota["total-over"] = {"status": st, "body": out}
    st, second = grant(t_q, "second.bin", 50)
    quota["second.policy"] = policy_facts(
        second.get("token", ""), second.get("key", ""), 50
    )
    # the bucket holds the credential to what is left: 51 bytes do not land
    st, out = spend(second, b"s" * 51)
    quota["second.spend-over-fsize-limit"] = {"status": st, "body": out}
    spend(second, b"s" * 50)
    st, out = public("POST", "/api/drop/register", t_q, {"key": second.get("key")})
    quota["second.register"] = {"status": st, "body": out}
    st, out = public("GET", "/api/drop", t_q)
    quota["info.exhausted"] = {"status": st, "body": out}
    st, out = grant(t_q, "third.bin", 1)
    quota["files-over"] = {"status": st, "body": out}
    st, out = public("PUT", "/api/drop/files/third.bin", t_q, raw=b"3")
    quota["put.exhausted"] = {"status": st, "body": out}
    with sqlite3.connect(db_path) as con:
        quota["used"] = con.execute(
            "SELECT used_files, used_bytes FROM upload_drops WHERE id = ?", (q_id,)
        ).fetchone()
        quota["used"] = list(quota["used"] or [])
    g.case("drop.quota", quota)

    print("\n== drops: quota is re-checked in the transaction that writes ==")
    # Two credentials, each fine on its own when it was signed, together over
    # the total: the second register is refused, its object is deleted, and the
    # quota is charged once.
    st, race, t_race = create(max_files=5, max_file_bytes=100, max_total_bytes=100)
    race_id = race.get("id") if isinstance(race, dict) else None
    st, ra = grant(t_race, "ra.bin", 60)
    st, rb = grant(t_race, "rb.bin", 60)
    spend(ra, b"a" * 60)
    spend(rb, b"b" * 60)
    st_a, out_a = public("POST", "/api/drop/register", t_race, {"key": ra.get("key")})
    st_b, out_b = public("POST", "/api/drop/register", t_race, {"key": rb.get("key")})
    st_c, out_c = public("POST", "/api/drop/register", t_race, {"key": rb.get("key")})
    with sqlite3.connect(db_path) as con:
        used = list(
            con.execute(
                "SELECT used_files, used_bytes FROM upload_drops WHERE id = ?",
                (race_id,),
            ).fetchone()
            or []
        )
        pending = con.execute(
            "SELECT count(*) FROM drop_pending WHERE key = ?", (rb.get("key"),)
        ).fetchone()[0]
        ledger = con.execute(
            "SELECT count(*) FROM pending_uploads WHERE key = ?", (rb.get("key"),)
        ).fetchone()[0]
    g.case(
        "drop.quota.final-transaction",
        {
            "first": {"status": st_a, "body": out_a},
            "second": {"status": st_b, "body": out_b},
            "second.retry": {"status": st_c, "body": out_c},
            "used": used,
            "refused_object_deleted": rb.get("key") not in fake.keys(),
            "refused_reservation_gone": pending == 0 and ledger == 0,
            "refused_row_absent": file_row("rb.bin") is None,
        },
    )

    print("\n== drops: one-step PUT (curl -T) ==")
    st, p, t_put = create(max_file_bytes=64)
    curl_bytes = b"one step upload body\n"
    st, out = public("PUT", "/api/drop/files/curl.txt", t_put, raw=curl_bytes)
    row = file_row("curl.txt")
    obj = fake.state().get((row or {}).get("key") or "", {})
    put_cases = {
        "put": {"status": st, "body": out},
        "row": _fresh_key(row),
        "bytes_round_trip": base64.b64decode(obj.get("content", "")) == curl_bytes,
    }
    st, out = public("PUT", "/api/drop/files/curl.txt", t_put, raw=b"second")
    put_cases["put.same-name"] = {"status": st, "body": out}
    put_cases["put.same-name.original-unchanged"] = file_row("curl.txt") == row
    st, out = raw_request(
        B,
        "PUT",
        "/api/drop/files/chunked.txt",
        [(DROP_HEADER, t_put), ("Transfer-Encoding", "chunked")],
        b"5\r\nhello\r\n0\r\n\r\n",
    )
    put_cases["put.chunked"] = {"status": st, "body": body_value(out)}
    public_bodies.append(out)
    st, out = raw_request(
        B, "PUT", "/api/drop/files/nolength.txt", [(DROP_HEADER, t_put)]
    )
    put_cases["put.no-content-length"] = {"status": st, "body": body_value(out)}
    public_bodies.append(out)
    st, out = public("PUT", "/api/drop/files/big.bin", t_put, raw=b"z" * 65)
    put_cases["put.over-file-limit"] = {"status": st, "body": out}
    put_cases["put.refusals-wrote-nothing"] = all(
        file_row(n) is None for n in ("chunked.txt", "nolength.txt", "big.bin")
    )
    g.case("drop.put", put_cases)

    print("\n== drops: expiry ==")
    st, e, t_exp = create()
    exp_id = e.get("id") if isinstance(e, dict) else None
    st, pre = grant(t_exp, "before-expiry.txt", 3)
    spend(pre, b"pre")
    expire(exp_id)
    expiry = {}
    st, out = public("GET", "/api/drop", t_exp)
    expiry["info"] = {"status": st, "body": out}
    st, out = grant(t_exp, "after.txt", 1)
    expiry["upload-token"] = {"status": st, "body": out}
    st, out = public("PUT", "/api/drop/files/after.txt", t_exp, raw=b"1")
    expiry["put"] = {"status": st, "body": out}
    st, out = public("POST", "/api/drop/register", t_exp, {"key": pre.get("key")})
    expiry["register-signed-before"] = {"status": st, "body": out}
    expiry["object-of-late-register-deleted"] = pre.get("key") not in fake.keys()
    expiry["no-row"] = file_row("before-expiry.txt") is None
    g.case("drop.expired", expiry)

    print("\n== drops: revocation ==")
    st, rv, t_rev = create()
    rev_id = rv.get("id") if isinstance(rv, dict) else None
    st, pre = grant(t_rev, "before-revoke.txt", 3)
    spend(pre, b"pre")
    revoked = {}
    st, out = admin("POST", "/api/fm/drops/revoke", {"id": rev_id})
    revoked["revoke"] = {"status": st, "body": out}
    st, out = admin("POST", "/api/fm/drops/revoke", {"id": rev_id})
    revoked["revoke.again"] = {"status": st, "body": out}
    st, out = admin("POST", "/api/fm/drops/revoke", {"id": 999999})
    revoked["revoke.unknown"] = {"status": st, "body": out}
    st, out = admin("POST", "/api/fm/drops/revoke", {"id": "1"})
    revoked["revoke.id-not-int"] = {"status": st, "body": out}
    st, out = public("GET", "/api/drop", t_rev)
    revoked["info"] = {"status": st, "body": out}
    st, out = grant(t_rev, "after.txt", 1)
    revoked["upload-token"] = {"status": st, "body": out}
    st, out = public("POST", "/api/drop/register", t_rev, {"key": pre.get("key")})
    revoked["register-signed-before"] = {"status": st, "body": out}
    revoked["object-of-late-register-deleted"] = pre.get("key") not in fake.keys()
    g.case("drop.revoked", revoked)

    print("\n== drops: the directory going away ==")
    st, _ = admin(
        "POST", "/api/fm/create-folder", {"path": SANDBOX, "name": "drop-gone"}
    )
    st, dm = admin("POST", "/api/fm/drops", {"dir": f"{SANDBOX}/drop-gone"})
    t_dm = dm.get("token", "") if isinstance(dm, dict) else ""
    admin(
        "POST",
        "/api/fm/delete",
        {"path": SANDBOX, "items": [{"path": f"{SANDBOX}/drop-gone"}]},
    )
    st, out = public("GET", "/api/drop", t_dm)
    g.case("drop.dir-missing", {"status": st, "body": out})

    print("\n== drops: admin list ==")
    st, listed = admin("GET", "/api/fm/drops")
    items = listed.get("items", []) if isinstance(listed, dict) else []
    g.case(
        "drop.list",
        {
            "status": st,
            "items": [shown(i) for i in items],
            "no_token_fields": all(
                "token" not in i and "token_hash" not in i for i in items
            ),
            "ids_descending": [i.get("id") for i in items]
            == sorted((i.get("id") for i in items), reverse=True),
        },
    )

    print("\n== drops: delete ==")
    deleted = {}
    st, out = admin("POST", "/api/fm/drops/delete", {"id": other_id})
    deleted["delete"] = {"status": st, "body": out}
    st, out = public("GET", "/api/drop", t_other)
    deleted["token-after-delete"] = {"status": st, "body": out}
    st, out = admin("POST", "/api/fm/drops/delete", {"id": other_id})
    deleted["delete.again"] = {"status": st, "body": out}
    with sqlite3.connect(db_path) as con:
        deleted["reservations_gone"] = (
            con.execute(
                "SELECT count(*) FROM drop_pending WHERE drop_id = ?", (other_id,)
            ).fetchone()[0]
            == 0
        )
    deleted["uploaded_file_kept"] = file_row("foreign.txt") is not None
    g.case("drop.delete", deleted)

    print("\n== drops: one-step PUT refused before the body is read ==")
    # Placed after the admin list so the extra link below does not shift it.
    # The route's guard runs put_file's own pre-spill judgement before the server
    # reads a byte. Each request announces 100 MB and never sends it, so an
    # answer at all proves the refusal did not wait for (or spill) the body;
    # without the guard every one of these hangs until the timeout.
    st, _tb, t_total = create(max_total_bytes=1000)
    unread = {}
    for label, drop_token in (
        ("unknown-token", "not-a-drop-token"),
        ("no-token", None),
        ("over-file-limit", t_put),
        ("over-remaining-total", t_total),
        ("expired", t_exp),
        ("revoked", t_rev),
    ):
        pairs = [] if drop_token is None else [(DROP_HEADER, drop_token)]
        st, _hd, text = refused_unread(B, "PUT", "/api/drop/files/unread.bin", pairs)
        public_bodies.append(text.encode())
        unread[label] = {"status": st, "body": body_value(text.encode())}
    st, _hd, text = refused_unread(
        B, "PUT", "/api/drop/files/a%01b.txt", [(DROP_HEADER, t_total)]
    )
    unread["control-in-name"] = {"status": st, "body": body_value(text.encode())}
    st, _hd, text = refused_unread(
        B, "PUT", "/api/drop/files/a%2Fb.txt", [(DROP_HEADER, t_total)]
    )
    unread["slash-in-name"] = {"status": st, "body": body_value(text.encode())}
    unread["nothing-written"] = file_row("unread.bin") is None
    g.case("drop.put.refused-unread", unread)

    print("\n== drops: uploader responses carry no key and no path ==")
    flat = b"\n".join(public_bodies).decode("utf-8", "replace")
    g.case(
        "drop.responses.minimal",
        {
            "responses_checked": len(public_bodies) > 40,
            "no_object_key": not any(k and k in flat for k in minted_keys),
            "no_storage_path": "qiniu://" not in flat and PREFIX not in flat,
        },
    )
    fake.clear_calls()


def main():  # noqa: C901 - a case list; splitting it would only hide the order
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()
    B = args.base
    token = os.environ.get("TOKEN")
    user, pw = os.environ.get("DAV_USER"), os.environ.get("DAV_PASS")
    fake_base = os.environ.get("FAKE_QINIU")
    db_path = os.environ.get("CONTRACT_DB_PATH")
    if not token or not user or not pw or not fake_base or not db_path:
        print(
            "FATAL: TOKEN / DAV_USER / DAV_PASS / FAKE_QINIU / "
            "CONTRACT_DB_PATH not set",
            file=sys.stderr,
        )
        return 2
    auth = f"{user}:{pw}"
    FAKE_BASE[0] = fake_base
    fake = Fake(fake_base)
    fake.reset()

    g = Golden("qiniu", record=args.record)
    if g.fatal:
        return g.finish()

    def dav_case(
        name, method, path, headers=None, body=None, with_facts=False, follow=True
    ):
        st, hd, raw = dav(
            B, method, path, auth, headers, body, OPENER if follow else NOFOLLOW
        )
        text = dav_normalize(raw.decode("utf-8", "replace"))
        kept = {k: hd[k] for k in KEEP if k in hd}
        if f"/{PREFIX}/" in path:  # a row this run created: its mtime is wall clock
            kept.pop("last-modified", None)
        value = {
            "method": method,
            "path": path,
            "status": st,
            "headers": scrub(kept),
            "body": scrub(text),
        }
        if with_facts:
            value["facts"] = scrub(facts(text))
        g.case(name, value)
        return st, hd, raw

    def api_case(name, method, path, body=None, headers=None):
        st, raw = api(B, method, path, token, body, headers)
        value = {"method": method, "path": path, "status": st}
        if body is not None:
            # the request is recorded too (a case is the pair, not the answer
            # alone) and needs the same scrubbing: /register echoes back a key
            # that upload-token minted this run
            value["request"] = scrub(body)
        value["body"] = scrub(body_value(raw))
        g.case(name, value)
        return st, raw

    def calls_case(name, note):
        """Pin what the object store was actually asked to do, then clear the log."""
        g.case(name, {"note": note, "calls": scrub(fake.calls())})
        fake.clear_calls()

    # ------------------------------------------------------------------ #
    print("\n== the fake itself: an unsigned request is refused ==")
    # Everything below rests on "the fake checks the signatures the backend
    # produced". That claim has to be a case, not a sentence in a docstring: a
    # fake that waved every request through would make each signed path green
    # while proving nothing about qiniu_sign.
    st, raw = api(fake_base, "GET", "/some-key?e=1&token=nonsense", None)
    g.case(
        "fake.rejects.badsignature",
        {"note": "harness self-check", "status": st, "body": body_value(raw)},
    )
    fake.clear_calls()

    print("\n== WebDAV COPY of a subtree (one qiniu copy per file row) ==")
    dav_case("mkcol.sandbox", "MKCOL", f"/dav/{PREFIX}")
    fake.clear_calls()
    dav_case(
        "copy.subdir",
        "COPY",
        "/dav/docs",
        {"Destination": f"/dav/{PREFIX}/docs-copy"},
    )
    # the tree: docs/ held img/ (a dir), img/cover.png and notes.txt
    dav_case(
        "copy.subdir.tree",
        "PROPFIND",
        f"/dav/{PREFIX}/docs-copy",
        {"Depth": "1"},
        with_facts=True,
    )
    dav_case(
        "copy.subdir.tree.sub",
        "PROPFIND",
        f"/dav/{PREFIX}/docs-copy/img",
        {"Depth": "1"},
        with_facts=True,
    )
    # ...and the objects: exactly two copies, from the two fixture keys, each to
    # a fresh key. A copy that skipped the nested file, or copied a directory
    # row, or duplicated a call, shows up here and nowhere else.
    calls_case("copy.subdir.objects", "one qiniu copy per file row, dirs excluded")
    # the duplicate is a real object: its bytes come back through the new key
    dav_case(
        "copy.subdir.bytes",
        "GET",
        f"/dav/{PREFIX}/docs-copy/notes.txt",
        {"User-Agent": PROXY_UA},
    )
    fake.clear_calls()

    # a single file, the degenerate case of the same walk: one row, one object
    dav_case(
        "copy.file",
        "COPY",
        "/dav/example.txt",
        {"Destination": f"/dav/{PREFIX}/example-copy.txt"},
    )
    calls_case("copy.file.calls", "one row, one object, a fresh key")

    print("\n== WebDAV COPY refused by the object store -> 502 ==")
    # The regression this pins: an object-store refusal used to surface as a 500
    # (errkind.dawn exists because of it). 599 is qiniu's own "operation failed".
    before_refused_state = fake.state()
    fake.refuse("copy", 599, '{"error":"contract fake refuses this copy"}')
    try:
        dav_case(
            "copy.upstream.502",
            "COPY",
            "/dav/docs",
            {"Destination": f"/dav/{PREFIX}/docs-refused"},
        )
    finally:
        fake.allow("copy")
    g.case(
        "copy.upstream.no-fresh-object",
        {
            "note": "an external copy refusal leaves neither destination metadata nor a fresh copied object",
            "objects_unchanged": fake.state() == before_refused_state,
            "destination_root_absent": file_metadata(db_path, f"{PREFIX}/docs-refused")
            is None,
        },
    )
    # one attempt, then it stops — a loop that swallowed the error would show two
    calls_case("copy.upstream.calls", "the walk aborts at the first refusal")
    # No metadata row is committed before the complete object-copy phase succeeds.
    dav_case(
        "copy.upstream.no-partial",
        "PROPFIND",
        f"/dav/{PREFIX}/docs-refused",
        {"Depth": "1"},
        with_facts=True,
    )

    print("\n== WebDAV GET / PUT / DELETE of an object ==")
    dav_case("get.file", "GET", "/dav/docs/notes.txt", {"User-Agent": PROXY_UA})
    dav_case(
        "get.file.range",
        "GET",
        "/dav/docs/notes.txt",
        {"User-Agent": PROXY_UA, "Range": "bytes=0-15"},
    )
    # a redirect-following client is 302'd straight at the signed URL instead —
    # recorded unfollowed, so the Location (and the fact that it is signed) is
    # the case rather than the bytes behind it
    dav_case(
        "get.file.302",
        "GET",
        "/dav/docs/notes.txt",
        {"User-Agent": REDIRECT_UA},
        follow=False,
    )
    dav_case("get.dir", "GET", "/dav/docs", {"User-Agent": PROXY_UA})
    calls_case(
        "get.file.calls", "two proxied reads (one ranged); the 302 fetches nothing"
    )

    dav_case(
        "put.new",
        "PUT",
        f"/dav/{PREFIX}/hello.txt",
        {"Content-Type": "text/plain"},
        b"hello from the contract\n",
    )
    calls_case("put.new.calls", "one upload, no delete: nothing was superseded")
    # the round trip the differential harness was built around: bytes in, bytes out
    dav_case(
        "put.new.bytes", "GET", f"/dav/{PREFIX}/hello.txt", {"User-Agent": PROXY_UA}
    )
    fake.clear_calls()
    dav_case(
        "put.overwrite",
        "PUT",
        f"/dav/{PREFIX}/hello.txt",
        {"Content-Type": "text/plain"},
        b"second write, longer than the first\n",
    )
    # the CDN invariant: an overwrite writes a NEW key and drops the old object,
    # never rewrites the key in place. Two ops, in that order, or it is broken.
    calls_case("put.overwrite.calls", "new key uploaded, superseded key dropped")
    dav_case(
        "put.overwrite.bytes",
        "GET",
        f"/dav/{PREFIX}/hello.txt",
        {"User-Agent": PROXY_UA},
    )
    fake.clear_calls()
    dav_case("delete.file", "DELETE", f"/dav/{PREFIX}/hello.txt")
    calls_case("delete.file.calls", "the object is deleted, not just the row")
    dav_case("delete.file.gone", "PROPFIND", f"/dav/{PREFIX}/hello.txt", {"Depth": "0"})

    print("\n== fm: the same subtree copy through the JSON API ==")
    # `sources` is a list of path STRINGS (str_list), not of {path} objects the
    # way /delete's `items` is — sending the wrong shape reads as an empty list
    # and the endpoint answers 200 having copied nothing.
    api_case(
        "fm.copy.subdir",
        "POST",
        "/api/fm/copy",
        {"path": SANDBOX, "destination": SANDBOX, "sources": ["qiniu://docs"]},
    )
    calls_case(
        "fm.copy.subdir.objects", "fm walks the subtree the same way WebDAV does"
    )
    api_case(
        "fm.mkdir",
        "POST",
        "/api/fm/create-folder",
        {"path": SANDBOX, "name": "refused"},
    )
    fake.refuse("copy", 599, '{"error":"contract fake refuses this copy"}')
    api_case(
        "fm.copy.502",
        "POST",
        "/api/fm/copy",
        {
            "path": SANDBOX,
            "destination": f"{SANDBOX}/refused",
            "sources": ["qiniu://docs"],
        },
    )
    fake.allow("copy")
    fake.clear_calls()

    print("\n== fm: create-file / save / content (upload_text + proxy read) ==")
    api_case(
        "fm.createfile",
        "POST",
        "/api/fm/create-file",
        {"path": SANDBOX, "name": "note.md"},
    )
    calls_case("fm.createfile.calls", "create-file writes a real (empty) object")
    api_case(
        "fm.save",
        "POST",
        "/api/fm/save",
        {"path": f"{SANDBOX}/note.md", "content": "# saved by the contract\n"},
    )
    # save is the same write-new-key-then-drop-the-old dance as PUT, through fm
    calls_case("fm.save.calls", "saved under a new key, the superseded one dropped")
    api_case(
        "fm.content",
        "GET",
        f"/api/fm/content?path={urllib.parse.quote(SANDBOX)}%2Fnote.md",
    )
    fake.clear_calls()

    print(
        "\n== fm: upload-token / register (the 'registered <=> really there' rule) =="
    )
    st, raw = api_case(
        "fm.uptok",
        "POST",
        "/api/fm/upload-token",
        {"path": SANDBOX, "name": "direct.bin"},
    )
    minted = json.loads(raw) if st == 200 else {}
    # register must refuse a key the bucket has never seen — that is the whole
    # point of the stat check, and it can only be exercised with a bucket
    api_case(
        "fm.register.missing",
        "POST",
        "/api/fm/register",
        {"path": f"{SANDBOX}/direct.bin", "key": minted.get("key", "missing-key")},
    )
    # now the object really is there (the browser's direct upload, simulated),
    # and qiniu's size/mime win over the client's claim
    fake.plant(
        minted.get("key", "missing-key"), "planted by the contract", "text/plain"
    )
    api_case(
        "fm.register.ok",
        "POST",
        "/api/fm/register",
        {
            "path": f"{SANDBOX}/direct.bin",
            "key": minted.get("key", "missing-key"),
            "size": 999999,
            "content_type": "application/x-not-what-qiniu-says",
        },
    )
    calls_case(
        "fm.register.calls",
        "two stats: the refusal above and the acceptance — nothing else touched",
    )

    print("\n== fm: delete drops the objects too ==")
    api_case(
        "fm.delete",
        "POST",
        "/api/fm/delete",
        {"path": SANDBOX, "items": [{"path": f"{SANDBOX}/direct.bin"}]},
    )
    calls_case("fm.delete.calls", "the row and the object go together")

    print("\n== WebDAV overwrite switches metadata before collecting old objects ==")
    copy_target_rel = f"{PREFIX}/copy-overwrite-target"
    copy_target_path = f"/dav/{copy_target_rel}"
    copy_setup_statuses = [
        dav(B, "MKCOL", copy_target_path, auth)[0],
        dav(
            B,
            "PUT",
            f"{copy_target_path}/old.txt",
            auth,
            {"Content-Type": "text/plain"},
            b"old COPY target bytes\n",
        )[0],
    ]
    copy_old = file_metadata(db_path, f"{copy_target_rel}/old.txt")
    copy_source = file_metadata(db_path, "docs/notes.txt")
    copy_source_object = (
        fake.state().get(copy_source["key"]) if copy_source is not None else None
    )
    fake.clear_calls()
    copy_overwrite_status, _, _ = dav_case(
        "copy.overwrite.204",
        "COPY",
        "/dav/docs",
        {"Destination": copy_target_path},
    )
    copy_target_note = file_metadata(db_path, f"{copy_target_rel}/notes.txt")
    copy_after_state = fake.state()
    g.case(
        "copy.overwrite.state",
        {
            "note": "COPY keeps the source, replaces the whole target, then drops old target objects",
            "setup_statuses": copy_setup_statuses,
            "copy_status": copy_overwrite_status,
            "source_metadata_preserved": file_metadata(db_path, "docs/notes.txt")
            == copy_source,
            "source_object_preserved": copy_source is not None
            and copy_after_state.get(copy_source["key"]) == copy_source_object,
            "target_has_fresh_notes_key": copy_source is not None
            and copy_target_note is not None
            and copy_target_note["key"] != copy_source["key"]
            and copy_target_note["key"] in copy_after_state,
            "old_target_metadata_absent": file_metadata(
                db_path, f"{copy_target_rel}/old.txt"
            )
            is None,
            "old_target_object_deleted": copy_old is not None
            and copy_old["key"] not in copy_after_state,
        },
    )
    calls_case(
        "copy.overwrite.calls",
        "fresh source copies are committed before the old target object is collected",
    )
    dav_case(
        "copy.overwrite.tree",
        "PROPFIND",
        copy_target_path,
        {"Depth": "1"},
        with_facts=True,
    )
    dav_case(
        "copy.overwrite.tree.sub",
        "PROPFIND",
        f"{copy_target_path}/img",
        {"Depth": "1"},
        with_facts=True,
    )
    dav_case(
        "copy.overwrite.old.gone",
        "PROPFIND",
        f"{copy_target_path}/old.txt",
        {"Depth": "0"},
    )
    dav_case(
        "copy.overwrite.target.bytes",
        "GET",
        f"{copy_target_path}/notes.txt",
        {"User-Agent": PROXY_UA},
    )
    dav_case(
        "copy.overwrite.source.retained",
        "GET",
        "/dav/docs/notes.txt",
        {"User-Agent": PROXY_UA},
    )
    fake.clear_calls()

    move_source_rel = f"{PREFIX}/move-overwrite-source"
    move_target_rel = f"{PREFIX}/move-overwrite-target"
    move_source_path = f"/dav/{move_source_rel}"
    move_target_path = f"/dav/{move_target_rel}"
    move_setup_statuses = [
        dav(B, "MKCOL", move_source_path, auth)[0],
        dav(B, "MKCOL", f"{move_source_path}/nested", auth)[0],
        dav(
            B,
            "PUT",
            f"{move_source_path}/nested/payload.txt",
            auth,
            {"Content-Type": "text/plain"},
            b"MOVE overwrite payload bytes\n",
        )[0],
        dav(B, "MKCOL", move_target_path, auth)[0],
        dav(
            B,
            "PUT",
            f"{move_target_path}/old.txt",
            auth,
            {"Content-Type": "text/plain"},
            b"old MOVE target bytes\n",
        )[0],
    ]
    move_source_file = file_metadata(db_path, f"{move_source_rel}/nested/payload.txt")
    move_old = file_metadata(db_path, f"{move_target_rel}/old.txt")
    fake.clear_calls()
    move_overwrite_status, _, _ = dav_case(
        "move.overwrite.204",
        "MOVE",
        move_source_path,
        {"Destination": move_target_path},
    )
    move_target_file = file_metadata(db_path, f"{move_target_rel}/nested/payload.txt")
    move_after_state = fake.state()
    g.case(
        "move.overwrite.state",
        {
            "note": "MOVE removes the source metadata, preserves its object key at the target, and collects old target objects",
            "setup_statuses": move_setup_statuses,
            "move_status": move_overwrite_status,
            "source_tree_absent": file_metadata(db_path, move_source_rel) is None,
            "source_file_absent": file_metadata(
                db_path, f"{move_source_rel}/nested/payload.txt"
            )
            is None,
            "target_reuses_source_key": move_source_file is not None
            and move_target_file is not None
            and move_target_file["key"] == move_source_file["key"],
            "source_object_preserved": move_source_file is not None
            and move_source_file["key"] in move_after_state,
            "old_target_metadata_absent": file_metadata(
                db_path, f"{move_target_rel}/old.txt"
            )
            is None,
            "old_target_object_deleted": move_old is not None
            and move_old["key"] not in move_after_state,
        },
    )
    calls_case(
        "move.overwrite.calls",
        "MOVE performs no object copy and collects only superseded target objects",
    )
    dav_case(
        "move.overwrite.source.gone",
        "PROPFIND",
        move_source_path,
        {"Depth": "0"},
    )
    dav_case(
        "move.overwrite.tree",
        "PROPFIND",
        move_target_path,
        {"Depth": "1"},
        with_facts=True,
    )
    dav_case(
        "move.overwrite.tree.sub",
        "PROPFIND",
        f"{move_target_path}/nested",
        {"Depth": "1"},
        with_facts=True,
    )
    dav_case(
        "move.overwrite.target.bytes",
        "GET",
        f"{move_target_path}/nested/payload.txt",
        {"User-Agent": PROXY_UA},
    )
    fake.clear_calls()

    unicode_path = f"{SANDBOX}/unicode.txt"
    unicode_content = "Dawn 保存：你好，世界 🌅🚀\n"
    unicode_bytes = unicode_content.encode("utf-8")
    api_case(
        "fm.createfile.utf8",
        "POST",
        "/api/fm/create-file",
        {"path": SANDBOX, "name": "unicode.txt"},
    )
    fake.clear_calls()
    unicode_save_status, _ = api_case(
        "fm.save.utf8",
        "POST",
        "/api/fm/save",
        {"path": unicode_path, "content": unicode_content},
    )
    calls_case(
        "fm.save.utf8.calls",
        "Unicode text is uploaded as UTF-8 under a fresh key before the empty object is collected",
    )
    listing_url = "/api/fm?" + urllib.parse.urlencode({"path": SANDBOX})
    listing_status, listing_raw = api(B, "GET", listing_url, token)
    try:
        listing_body = json.loads(listing_raw)
    except (TypeError, ValueError, UnicodeDecodeError):
        listing_body = {}
    listing_files = (
        listing_body.get("files", []) if isinstance(listing_body, dict) else []
    )
    unicode_entry = next(
        (
            entry
            for entry in listing_files
            if isinstance(entry, dict) and entry.get("path") == unicode_path
        ),
        None,
    )
    content_url = "/api/fm/content?" + urllib.parse.urlencode({"path": unicode_path})
    unicode_content_status, unicode_raw = api(B, "GET", content_url, token)
    listed_size = (
        unicode_entry.get("file_size") if isinstance(unicode_entry, dict) else None
    )
    g.case(
        "fm.save.utf8-byte-size",
        {
            "note": "FM save reports UTF-8 byte length and returns the exact uploaded bytes",
            "save_status": unicode_save_status,
            "listing_status": listing_status,
            "content_status": unicode_content_status,
            "expected_utf8_size": len(unicode_bytes),
            "listed_size": listed_size,
            "size_matches_utf8_bytes": listed_size == len(unicode_bytes),
            "expected_base64": base64.b64encode(unicode_bytes).decode(),
            "read_back_base64": base64.b64encode(unicode_raw).decode(),
            "bytes_round_trip": unicode_raw == unicode_bytes,
        },
    )
    fake.clear_calls()

    print("\n== WebDAV direct file parents stop every production write path ==")
    before_direct_parent_keys = fake.keys()
    direct_parent_before = file_metadata(db_path, "legacy-parent.txt")
    move_source_before = file_metadata(db_path, "empty-dir")
    copy_source_before = file_metadata(db_path, "docs/notes.txt")
    put_status, _, _ = dav(
        B,
        "PUT",
        "/dav/legacy-parent.txt/new-put.txt",
        auth,
        body=b"must not upload",
    )
    after_put = {
        "parent": file_metadata(db_path, "legacy-parent.txt"),
        "target": file_metadata(db_path, "legacy-parent.txt/new-put.txt"),
    }
    mkcol_status, _, _ = dav(
        B,
        "MKCOL",
        "/dav/legacy-parent.txt/new-col",
        auth,
    )
    after_mkcol = {
        "parent": file_metadata(db_path, "legacy-parent.txt"),
        "target": file_metadata(db_path, "legacy-parent.txt/new-col"),
    }
    move_status, _, _ = dav(
        B,
        "MOVE",
        "/dav/empty-dir",
        auth,
        {"Destination": "/dav/legacy-parent.txt/new-move"},
    )
    after_move = {
        "parent": file_metadata(db_path, "legacy-parent.txt"),
        "target": file_metadata(db_path, "legacy-parent.txt/new-move"),
        "source": file_metadata(db_path, "empty-dir"),
    }
    copy_status, _, _ = dav(
        B,
        "COPY",
        "/dav/docs/notes.txt",
        auth,
        {"Destination": "/dav/legacy-parent.txt/new-copy.txt"},
    )
    after_copy = {
        "parent": file_metadata(db_path, "legacy-parent.txt"),
        "target": file_metadata(db_path, "legacy-parent.txt/new-copy.txt"),
        "source": file_metadata(db_path, "docs/notes.txt"),
    }
    g.case(
        "write.dest.file-parent.preflight",
        {
            "note": "PUT MKCOL MOVE and COPY reject a direct file parent before effects",
            "put_status": put_status,
            "mkcol_status": mkcol_status,
            "move_status": move_status,
            "copy_status": copy_status,
            "parent_before": direct_parent_before,
            "after_put": after_put,
            "after_mkcol": after_mkcol,
            "after_move": after_move,
            "after_copy": after_copy,
            "metadata_equivalent_after_each_method": all(
                observation["parent"] == direct_parent_before
                and observation["target"] is None
                for observation in (after_put, after_mkcol, after_move, after_copy)
            )
            and after_move["source"] == move_source_before
            and after_copy["source"] == copy_source_before,
            "objects_unchanged": fake.keys() == before_direct_parent_keys,
            "object_calls": scrub(fake.calls()),
        },
    )
    fake.clear_calls()

    print("\n== a legacy content_type never leaves the process as written ==")
    # files.content_type predates every check this backend runs, so these three
    # rows are written straight into SQLite rather than through an endpoint —
    # a row like this cannot be created through the API any more, and the point
    # is what happens when one is already there.
    #
    # The wire is read over a plain socket because urllib reassembles the
    # response before handing it over: "the header block is well formed and
    # carries exactly one content type" is not a claim a parsed response can
    # make.
    legacy_bytes = "legacy-bytes"
    mime_probe = {}
    for label, stored in (
        ("empty", ""),
        ("crlf", "text/plain\r\nX-Injected: 1"),
        ("low-byte-alias", "text/plainčĊX-Injected: 1"),
    ):
        rel = f"{PREFIX}/legacy-mime-{label}.bin"
        key = f"legacy-mime-{label}-key"
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                'INSERT INTO files (path, is_dir, "key", content_type, size, '
                "created_at, updated_at) VALUES (?, 0, ?, ?, ?, "
                "'2026-08-12 00:00:00', '2026-08-12 00:00:00')",
                (rel, key, stored, len(legacy_bytes)),
            )
        fake.plant(key, legacy_bytes, "application/octet-stream")
        basic = "Basic " + base64.b64encode(auth.encode()).decode()
        probes = {
            "dav.head": ("HEAD", f"/dav/{rel}", [("Authorization", basic)]),
            "dav.get": (
                "GET",
                f"/dav/{rel}",
                [("Authorization", basic), ("User-Agent", PROXY_UA)],
            ),
            "fm.content": (
                "GET",
                "/api/fm/content?" + urllib.parse.urlencode({"path": f"qiniu://{rel}"}),
                [("Authorization", f"Bearer {token}")],
            ),
            "dav.propfind": (
                "PROPFIND",
                f"/dav/{rel}",
                [("Authorization", basic), ("Depth", "0")],
            ),
        }
        entry = {"stored_content_type": stored}
        for probe_name, (method, path, header_pairs) in probes.items():
            head, raw = raw_http(B, method, path, header_pairs)
            report = wire_report(head, raw)
            report.pop("kept_headers", None)
            if probe_name == "dav.propfind":
                body = raw.decode("utf-8", "replace")
                report["getcontenttype_values"] = re.findall(
                    r"<D:getcontenttype>(.*?)</D:getcontenttype>", body
                )
            entry[probe_name] = report
        mime_probe[label] = entry

    # Saving reuses the row's own content type, so a dirty row is where a save
    # would put an injected header into the multipart body posted to qiniu — and
    # would then write the dirty value back, keeping the row dirty forever.
    save_rel = f"{PREFIX}/legacy-mime-crlf.bin"
    fake.clear_calls()
    save_status, save_raw = api(
        B,
        "POST",
        "/api/fm/save",
        token,
        {"path": f"qiniu://{save_rel}", "content": "rewritten"},
    )
    # The freshly minted key is normalized locally rather than through scrub():
    # scrub's aliases are numbered in order of first appearance across the whole
    # run, so a change anywhere earlier renumbers them and this case would go red
    # for something that has nothing to do with content types. Distinctness is not
    # what is being pinned here — the content type is.
    mime_probe["save"] = {
        "note": "the remote multipart and the rewritten row both carry the fallback",
        "status": save_status,
        "body": body_value(save_raw),
        "calls": _fresh_key(fake.calls()),
        "row_after": _fresh_key(file_metadata(db_path, save_rel)),
    }
    fake.clear_calls()
    g.case("fm.persisted-mime.fail-safe", mime_probe)

    print("\n== fm: the minted upload token is in date, and it spends ==")
    # The rest of the upload-token cases pin what the endpoint answers. What they
    # cannot see is whether the credential works, because the whole point of
    # direct upload is that the bytes never come back through this backend: an
    # already-expired deadline, or one years out, produced exactly the same 200
    # and the same well-formed token. So this case reads the window out of the
    # policy and then spends the token against the bucket.
    #
    # The deadline itself is wall clock and is never recorded — only whether it
    # is in the future and inside a day, which is the claim.
    mint_status, mint_raw = api(
        B,
        "POST",
        "/api/fm/upload-token",
        token,
        {"path": SANDBOX, "name": "deadline-window.bin"},
    )
    minted_window = json.loads(mint_raw) if mint_status == 200 else {}
    window_key = minted_window.get("key", "")
    policy = put_policy(minted_window.get("token", ""))
    deadline = policy.get("deadline")
    issued_at = int(time.time())
    fake.clear_calls()
    spend_status, spend_body = spend_upload_token(
        fake_base, minted_window.get("token", ""), window_key, b"spent in window\n"
    )
    g.case(
        "fm.upload-token.deadline-window",
        {
            "note": "the policy deadline is ahead of now and inside a day, and the bucket takes the token",
            "mint_status": mint_status,
            "policy_keys": sorted(policy),
            "scope_is_bucket_and_key": policy.get("scope")
            == f"{contract_qiniu_fake.FAKE_BUCKET}:{window_key}",
            "deadline_valid": isinstance(deadline, int)
            and not isinstance(deadline, bool)
            and issued_at
            < deadline
            <= issued_at + contract_qiniu_fake.MAX_TOKEN_LIFETIME,
            "spend_status": spend_status,
            "spend_echoes_key": isinstance(spend_body, dict)
            and spend_body.get("key") == window_key,
            "object_stored": window_key != "" and window_key in fake.keys(),
        },
    )
    fake.clear_calls()

    print("\n== fm: an object store that goes quiet, not one that refuses ==")

    # Last on purpose: it mints a key, and scrub() numbers keys in order of
    # first appearance, so putting it anywhere earlier renumbers every later
    # case's <keyN> aliases for no reason.
    #
    # A peer that accepts the connection and then says nothing is a different
    # failure from a peer that refuses us, and until #252 both left here as 502.
    # This is the one place the distinction can be watched end to end: the fake
    # goes quiet past the management budget, so the backend gives up on its own
    # deadline and the status on the wire is the whole assertion.
    #
    # The stall is longer than the budget rather than endless, so a lost
    # deadline is a late 200 (a red) instead of a wedged run. The case costs the
    # budget in wall clock and nothing else -- the request returns the moment
    # the backend stops waiting.
    st, raw = api(
        B, "POST", "/api/fm/upload-token", token, {"path": SANDBOX, "name": "quiet.bin"}
    )
    quiet_key = (
        json.loads(raw).get("key", "missing-key") if st == 200 else "missing-key"
    )
    fake.stall("stat", 15)
    st, raw = api(
        B,
        "POST",
        "/api/fm/register",
        token,
        {"path": f"{SANDBOX}/quiet.bin", "key": quiet_key},
        timeout=60,
    )
    fake.unstall("stat")
    g.case(
        "fm.register.quiet_peer",
        {
            "method": "POST",
            "path": "/api/fm/register",
            "status": st,
            "note": "the object store accepted the connection and never answered",
            "body": normalize_timeout(scrub(body_value(raw))),
        },
    )
    # The key is normalized here rather than through scrub(), for the reason
    # spelled out at fm.upload-token.deadline-window: scrub() numbers keys in
    # order of first appearance across the whole run, so a scrubbed alias in
    # this case would move whenever anything earlier minted one more key. That
    # is not hypothetical -- the accept-file-parent mutant in
    # scripts/webdav-destination-mutants/ makes exactly one extra object call,
    # and it turned this case red as collateral before the alias came out.
    # Which key it was is not the assertion anyway; that a stat was made is.
    quiet_calls = [
        {**call, "key": "<quiet key>"} if call.get("key") == quiet_key else call
        for call in fake.calls()
    ]
    g.case(
        "fm.register.quiet_peer.calls",
        {
            "note": "the stat was really made; the answer is what never came",
            "calls": quiet_calls,
        },
    )
    fake.clear_calls()

    drop_cases(g, B, token, auth, fake, fake_base, db_path)

    # What is still not covered here, and why it is not a fake's job.
    g.skip(
        "fm.stats",
        "bucket usage comes from the qiniu billing/space APIs (qiniu_stats.dawn), "
        "which this fake does not model — needs QINIU_* credentials",
    )

    return g.finish()


if __name__ == "__main__":
    sys.exit(main())
