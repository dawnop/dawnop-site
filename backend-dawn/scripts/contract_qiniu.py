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

    TOKEN=... DAV_USER=... DAV_PASS=... FAKE_QINIU=http://127.0.0.1:18100 \\
        python3 contract_qiniu.py --base http://127.0.0.1:18002
"""

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import contract_fixture
from contract_golden import TRANSPORT_STATUS, Golden, transport_error
from contract_webdav import KEEP_HEADERS, PREFIX, facts
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

    def calls(self):
        return self._get("/__fake/calls")["calls"]

    def keys(self):
        return self._get("/__fake/objects")["keys"]


def main():  # noqa: C901 - a case list; splitting it would only hide the order
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()
    B = args.base
    token = os.environ.get("TOKEN")
    user, pw = os.environ.get("DAV_USER"), os.environ.get("DAV_PASS")
    fake_base = os.environ.get("FAKE_QINIU")
    if not token or not user or not pw or not fake_base:
        print(
            "FATAL: TOKEN / DAV_USER / DAV_PASS / FAKE_QINIU not set", file=sys.stderr
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

    print("\n== WebDAV overwrite validates a legacy file parent before purge ==")
    legacy_row = next(
        row
        for row in contract_fixture.FILES
        if row[1] == "legacy-parent.txt/existing-target.txt"
    )
    legacy_key = legacy_row[3]
    overwrite_f_status, _, _ = dav(
        B,
        "COPY",
        "/dav/empty-dir",
        auth,
        {
            "Destination": "/dav/legacy-parent.txt/existing-target.txt",
            "Overwrite": "F",
        },
    )
    copy_status, _, _ = dav(
        B,
        "COPY",
        "/dav/empty-dir",
        auth,
        {"Destination": "/dav/legacy-parent.txt/existing-target.txt"},
    )
    row_status, _, _ = dav(
        B,
        "PROPFIND",
        "/dav/legacy-parent.txt/existing-target.txt",
        auth,
        {"Depth": "0"},
    )
    g.case(
        "copy.dest.file-parent-existing.preserved",
        {
            "note": "409 precedes purge for a legacy child whose parent is a file",
            "overwrite_f_status": overwrite_f_status,
            "copy_status": copy_status,
            "row_status": row_status,
            "object_present": legacy_key in fake.keys(),
            "object_calls": scrub(fake.calls()),
        },
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
    fake.refuse("copy", 599, '{"error":"contract fake refuses this copy"}')
    dav_case(
        "copy.upstream.502",
        "COPY",
        "/dav/docs",
        {"Destination": f"/dav/{PREFIX}/docs-refused"},
    )
    # one attempt, then it stops — a loop that swallowed the error would show two
    calls_case("copy.upstream.calls", "the walk aborts at the first refusal")
    # COPY is not transactional: what the aborted walk left behind is recorded
    # rather than asserted away, so a future change to that is a visible diff.
    dav_case(
        "copy.upstream.partial",
        "PROPFIND",
        f"/dav/{PREFIX}/docs-refused",
        {"Depth": "1"},
        with_facts=True,
    )
    fake.allow("copy")

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

    # What is still not covered here, and why it is not a fake's job.
    g.skip(
        "fm.stats",
        "bucket usage comes from the qiniu billing/space APIs (qiniu_stats.dawn), "
        "which this fake does not model — needs QINIU_* credentials",
    )

    return g.finish()


if __name__ == "__main__":
    sys.exit(main())
