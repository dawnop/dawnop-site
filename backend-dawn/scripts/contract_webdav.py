#!/usr/bin/env python3
"""WebDAV contract: the methods that do not need object storage, on a golden.

What this can and cannot cover — stated up front, because the honest boundary
is the interesting part of this file:

  covered (metadata lives in SQLite, so the fixture owns the answer)
      OPTIONS, the Basic-auth challenge, PROPFIND (root/dir/file/404/Depth
      0+1/non-ASCII/X-Dav-Prefix), HEAD, MKCOL (+ duplicate, + missing parent,
      + root), MOVE of a file and of a directory (rename only rewrites `path`),
      the MOVE guards (no Destination / onto self / Overwrite: F), COPY of an
      empty directory, LOCK/UNLOCK, DELETE of a directory, DELETE of the root.

  NOT covered (the request reaches qiniu, and a contract run has no credentials)
      GET (fetches bytes), PUT (uploads bytes), COPY of a file (really copies
      the object), DELETE of a file (deletes the object). All four are named
      skips, listed in golden/webdav.json and printed by every run. Measured,
      not assumed: with empty credentials each one panics in the HMAC signer
      ("Empty key") and answers 500 — a state not worth recording.

That leaves the PUT->GET byte round trip, which was the differential harness's
centrepiece, outside CI. It is the one part of this API that cannot be tested
without a bucket; pretending otherwise by asserting only OPTIONS would be worse
than saying so.

Normally driven by contract_run.py. Standalone:

    DAV_USER=... DAV_PASS=... python3 contract_webdav.py --base http://127.0.0.1:18001
"""

import argparse
import base64
import os
import re
import sys
import urllib.error
import urllib.request

from contract_golden import TRANSPORT_STATUS, Golden, transport_error

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Response headers worth pinning (Date/Server are transport noise).
KEEP_HEADERS = (
    "dav",
    "allow",
    "content-type",
    "content-length",
    "etag",
    "last-modified",
    "lock-token",
    "www-authenticate",
)

# Two wall-clock sites, both found by recording the file twice and diffing —
# neither was predicted by reading the code:
#
#  1. Rows this script creates (MKCOL/MOVE/COPY under dav-test/) get their
#     created_at/updated_at from SQLite's CURRENT_TIMESTAMP, so their
#     <getlastmodified>/<creationdate> move every run. The virtual root is the
#     same story with no row at all: webdav.dawn stamps it with now().
#     Timestamps on *fixture* rows stay pinned — that is the distinction the
#     href test below draws, and it is why the normalization is per-response
#     rather than a blanket "drop all dates".
#  2. LOCK mints a fresh uuid per grant (a fake lock; nothing tracks it), in the
#     body AND in the Lock-Token header.
#
# Both are replaced, not deleted: a response that stopped carrying the property
# still fails.
_RESPONSE = re.compile(r"<D:response>.*?</D:response>", re.S)
_HREF = re.compile(r"<D:href>(.*?)</D:href>", re.S)
_STAMP = re.compile(
    r"<D:(getlastmodified|creationdate)>[^<]*</D:(?:getlastmodified|creationdate)>"
)
_LOCKTOKEN = re.compile(r"opaquelocktoken:[0-9a-fA-F-]+")

PREFIX = "dav-test"  # sandbox subtree for the mutating cases


def _run_created(href: str) -> bool:
    """True for the virtual root and for anything this run created under dav-test/."""
    h = href.strip()
    if h in ("/", "/dav/"):
        return True
    rel = h[len("/dav/") :] if h.startswith("/dav/") else h.lstrip("/")
    return rel.split("/", 1)[0] == PREFIX


def normalize(text: str) -> str:
    def per_response(m):
        block = m.group(0)
        href = _HREF.search(block)
        if href and _run_created(href.group(1)):
            return _STAMP.sub(r"<D:\1>WALL-CLOCK</D:\1>", block)
        return block

    return _LOCKTOKEN.sub("opaquelocktoken:<uuid>", _RESPONSE.sub(per_response, text))


def dav(base, method, path, auth, headers=None, body=None, timeout=30):
    hdrs = dict(headers or {})
    if auth is not None:
        hdrs["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
    data = body if isinstance(body, (bytes, type(None))) else body.encode()
    r = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
    try:
        with OPENER.open(r, timeout=timeout) as resp:
            return (
                resp.status,
                {k.lower(): v for k, v in resp.headers.items()},
                resp.read(),
            )
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read()
    except Exception as e:  # noqa: BLE001 - transport failure is a case failure
        return TRANSPORT_STATUS, {}, transport_error(e).encode()


def facts(xml: str) -> dict:
    """The PROPFIND properties a reader actually checks, pulled out for legible diffs.

    The raw XML is recorded too — this is the readable half of the same fact.
    """

    def all_of(tag):
        return re.findall(
            rf"<(?:\w+:)?{tag}>\s*(.*?)\s*</(?:\w+:)?{tag}>", xml, re.I | re.S
        )

    return {
        "hrefs": all_of("href"),
        "displaynames": all_of("displayname"),
        "contentlengths": all_of("getcontentlength"),
        "contenttypes": all_of("getcontenttype"),
        "lastmodified": all_of("getlastmodified"),
        "collections": xml.count("<D:collection/>"),
        "statuses": sorted(set(all_of("status"))),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()
    B = args.base
    user, pw = os.environ.get("DAV_USER"), os.environ.get("DAV_PASS")
    if not user or not pw:
        print("FATAL: DAV_USER/DAV_PASS not set", file=sys.stderr)
        return 2
    auth = f"{user}:{pw}"

    g = Golden("webdav", record=args.record)
    if g.fatal:
        return g.finish()

    def case(name, method, path, headers=None, body=None, creds=auth, with_facts=False):
        st, hd, raw = dav(B, method, path, creds, headers, body)
        text = normalize(raw.decode("utf-8", "replace"))
        value = {
            "method": method,
            "path": path,
            "status": st,
            "headers": {
                k: _LOCKTOKEN.sub("opaquelocktoken:<uuid>", hd[k])
                for k in KEEP_HEADERS
                if k in hd
            },
            "body": text,
        }
        if with_facts:
            value["facts"] = facts(text)
        g.case(name, value)
        return st, hd

    print("\n== OPTIONS / auth challenge ==")
    case("options.root", "OPTIONS", "/dav/")
    case("options.file", "OPTIONS", "/dav/docs/notes.txt")
    case("propfind.unauth", "PROPFIND", "/dav/", {"Depth": "0"}, creds=None)
    case(
        "propfind.badpass",
        "PROPFIND",
        "/dav/",
        {"Depth": "0"},
        creds=f"{user}:wrong-password",
    )

    print("\n== PROPFIND (fixture tree) ==")
    case("propfind.root.d0", "PROPFIND", "/dav/", {"Depth": "0"}, with_facts=True)
    case("propfind.root.d1", "PROPFIND", "/dav/", {"Depth": "1"}, with_facts=True)
    case("propfind.dir.d1", "PROPFIND", "/dav/docs", {"Depth": "1"}, with_facts=True)
    case(
        "propfind.file.d0",
        "PROPFIND",
        "/dav/docs/notes.txt",
        {"Depth": "0"},
        with_facts=True,
    )
    # non-ASCII path: href must come back percent-encoded
    case(
        "propfind.unicode",
        "PROPFIND",
        "/dav/%E5%9B%BE%E7%89%87",
        {"Depth": "1"},
        with_facts=True,
    )
    case("propfind.404", "PROPFIND", "/dav/zzz-nope", {"Depth": "0"})
    # the subdomain vhost sends X-Dav-Prefix: / so hrefs come back as /foo.txt
    # rather than /dav/foo.txt (otherwise dav.dawnop.com serves /dav/dav/...)
    case(
        "propfind.prefix",
        "PROPFIND",
        "/dav/",
        {"Depth": "1", "X-Dav-Prefix": "/"},
        with_facts=True,
    )

    print("\n== HEAD (metadata only, no bytes) ==")
    case("head.file", "HEAD", "/dav/docs/notes.txt")
    case("head.dir", "HEAD", "/dav/docs")
    case("head.404", "HEAD", "/dav/zzz-nope.bin")

    print("\n== MKCOL ==")
    case("mkcol.new", "MKCOL", f"/dav/{PREFIX}")
    case("mkcol.dup", "MKCOL", f"/dav/{PREFIX}")
    case("mkcol.sub", "MKCOL", f"/dav/{PREFIX}/sub")
    case("mkcol.noparent", "MKCOL", "/dav/zzz-missing-parent/child")
    case("mkcol.root", "MKCOL", "/dav/")
    case(
        "propfind.sandbox",
        "PROPFIND",
        f"/dav/{PREFIX}",
        {"Depth": "1"},
        with_facts=True,
    )

    print("\n== MOVE (path rewrite only — no object is touched) ==")
    case("move.nodest", "MOVE", f"/dav/{PREFIX}/sub")
    case(
        "move.self", "MOVE", f"/dav/{PREFIX}/sub", {"Destination": f"/dav/{PREFIX}/sub"}
    )
    case(
        "move.intoself",
        "MOVE",
        f"/dav/{PREFIX}",
        {"Destination": f"/dav/{PREFIX}/deeper"},
    )
    case("move.404", "MOVE", "/dav/zzz-nope", {"Destination": f"/dav/{PREFIX}/x"})
    case(
        "move.dir",
        "MOVE",
        f"/dav/{PREFIX}/sub",
        {"Destination": f"/dav/{PREFIX}/renamed"},
    )
    case(
        "move.file",
        "MOVE",
        "/dav/docs/notes.txt",
        {"Destination": f"/dav/{PREFIX}/renamed/notes.txt"},
    )
    case("propfind.moved.old", "PROPFIND", "/dav/docs/notes.txt", {"Depth": "0"})
    case(
        "propfind.moved.new",
        "PROPFIND",
        f"/dav/{PREFIX}/renamed",
        {"Depth": "1"},
        with_facts=True,
    )
    case(
        "move.overwriteF",
        "MOVE",
        f"/dav/{PREFIX}/renamed",
        {"Destination": "/dav/docs", "Overwrite": "F"},
    )

    print("\n== COPY (empty collection: nothing to copy in the bucket) ==")
    case(
        "copy.emptydir",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav/{PREFIX}/copied"},
    )
    case(
        "propfind.copied",
        "PROPFIND",
        f"/dav/{PREFIX}/copied",
        {"Depth": "0"},
        with_facts=True,
    )

    print("\n== LOCK / UNLOCK (fake lock: always granted, never tracked) ==")
    lockbody = (
        '<?xml version="1.0"?><d:lockinfo xmlns:d="DAV:">'
        "<d:lockscope><d:exclusive/></d:lockscope>"
        "<d:locktype><d:write/></d:locktype></d:lockinfo>"
    )
    _, hd = case(
        "lock.grant",
        "LOCK",
        f"/dav/{PREFIX}/renamed",
        {"Content-Type": "application/xml"},
        lockbody,
    )
    token = hd.get("lock-token", "")
    case(
        "unlock",
        "UNLOCK",
        f"/dav/{PREFIX}/renamed",
        {"Lock-Token": token or "opaquelocktoken:x"},
    )

    print("\n== DELETE ==")
    case("delete.root", "DELETE", "/dav/")
    case("delete.404", "DELETE", "/dav/zzz-nope")
    case("delete.dir", "DELETE", f"/dav/{PREFIX}")
    case("propfind.deleted", "PROPFIND", f"/dav/{PREFIX}", {"Depth": "0"})

    # The four that need a bucket. Measured with empty credentials: each panics
    # in the qiniu HMAC signer and answers 500, so there is nothing here worth
    # recording — only a note that this is where the coverage stops.
    g.skip("get.file", "GET streams object bytes — needs QINIU_* credentials")
    g.skip("put.new", "PUT uploads object bytes — needs QINIU_* credentials")
    g.skip(
        "copy.file", "COPY of a file duplicates the object — needs QINIU_* credentials"
    )
    g.skip(
        "delete.file", "DELETE of a file removes the object — needs QINIU_* credentials"
    )

    return g.finish()


if __name__ == "__main__":
    sys.exit(main())
