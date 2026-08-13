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
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

from contract_golden import TRANSPORT_STATUS, Golden, transport_error
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
    mime_probe["save"] = {
        "note": "the remote multipart and the rewritten row both carry the fallback",
        "status": save_status,
        "body": scrub(body_value(save_raw)),
        "calls": scrub(fake.calls()),
        "row_after": scrub(file_metadata(db_path, save_rel)),
    }
    fake.clear_calls()
    g.case("fm.persisted-mime.fail-safe", mime_probe)

    # What is still not covered here, and why it is not a fake's job.
    g.skip(
        "fm.stats",
        "bucket usage comes from the qiniu billing/space APIs (qiniu_stats.dawn), "
        "which this fake does not model — needs QINIU_* credentials",
    )

    return g.finish()


if __name__ == "__main__":
    sys.exit(main())
