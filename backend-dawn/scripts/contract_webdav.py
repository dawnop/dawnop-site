#!/usr/bin/env python3
"""WebDAV contract: the methods that do not need object storage, on a golden.

What this can and cannot cover — stated up front, because the honest boundary
is the interesting part of this file:

  covered (metadata lives in SQLite, so the fixture owns the answer)
      OPTIONS, the Basic-auth challenge, PROPFIND (root/dir/file/404/Depth
      0+1/non-ASCII/encoded-slash/X-Dav-Prefix), HEAD, MKCOL (+ duplicate,
      + missing parent, + root), MOVE of a file and of a directory (rename only
      rewrites `path`), the MOVE guards (no Destination / onto self /
      Overwrite: F / an encoded slash / dot segments in the Destination), COPY
      of an empty directory (plus source preservation after a rejected target,
      the same Destination decoding: %2E, a literal '+'),
      LOCK/UNLOCK, DELETE of a directory, DELETE of the root.

  NOT covered HERE (the request reaches qiniu, and this backend has no
  credentials)
      GET (fetches bytes), PUT (uploads bytes), COPY of a file (really copies
      the object), DELETE of a file (deletes the object). All four are named
      skips, listed in golden/webdav.json and printed by every run. Measured,
      not assumed: with empty credentials each one panics in the HMAC signer
      ("Empty key") and answers 500 — a state not worth recording.

      They are not uncovered, though: contract_qiniu.py runs them (plus COPY of
      a *subtree*, and the PUT->GET byte round trip that was the differential
      harness's centrepiece) against a fake bucket on loopback. The split is on
      purpose — these bytes are what an unconfigured backend answers, those are
      what a configured one does, and the two goldens carry different
      environment fingerprints so neither can be recorded over the other.

Normally driven by contract_run.py. Standalone:

    DAV_USER=... DAV_PASS=... python3 contract_webdav.py --base http://127.0.0.1:18001
"""

import argparse
import base64
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
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


# A header line the wire format allows: a token, a colon, then no control
# characters. Anything the server emits that fails this is a malformed header,
# which is exactly what a stored CRLF in a content type produces.
_HEADER_LINE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+:[ \t]?[\x20-\x7e]*$")
# Header values that move every run and say nothing about the contract.
_VOLATILE_HEADERS = ("date", "server", "keep-alive", "connection")


def raw_http(base, method, path, header_pairs, timeout=15):
    """One request over a plain socket, answered as (head bytes, body bytes).

    urllib cannot send the same header twice and reassembles the response before
    handing it over, so neither a repeated Depth header nor a malformed response
    header line is expressible through it. Both are the point of the cases that
    call this.
    """
    parts = urllib.parse.urlsplit(base)
    host = parts.hostname
    port = parts.port or 80
    lines = [
        f"{method} {path} HTTP/1.1",
        f"Host: {host}:{port}",
        "Connection: close",
        "Content-Length: 0",
        *(f"{k}: {v}" for k, v in header_pairs),
    ]
    request = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(request)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        head, _, body = buf.partition(b"\r\n\r\n")
        want = None if method == "HEAD" else _content_length(head)
        while want is None or len(body) < want:
            chunk = sock.recv(65536)
            if not chunk:
                break
            body += chunk
    return head, body


def _content_length(head: bytes):
    for line in head.decode("latin-1").split("\r\n")[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() == "content-length":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


def wire_report(head: bytes, body: bytes) -> dict:
    """What a response looked like on the wire, with the per-run values dropped.

    Header *names* are kept in order (an injected header changes the list even
    when nobody looks at its value), the content-type values are kept in full
    (the whole point is that there is exactly one and it is the fallback), and
    every line is checked against the wire grammar.
    """
    text = head.decode("latin-1")
    lines = text.split("\r\n")
    fields = [line for line in lines[1:] if line]
    named = [
        (line.split(":", 1)[0].strip().lower(), line.split(":", 1)[-1].strip())
        for line in fields
    ]
    return {
        "status_line": lines[0] if lines else "",
        "header_names": [name for name, _ in named],
        "content_type_values": [v for name, v in named if name == "content-type"],
        "kept_headers": {
            name: value for name, value in named if name not in _VOLATILE_HEADERS
        },
        "malformed_header_lines": [
            line for line in fields if not _HEADER_LINE.match(line)
        ],
        "mentions_injected": "injected" in text.lower(),
        "body_size": len(body),
    }


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
    authority = urllib.parse.urlsplit(B).netloc
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
    # an encoded slash inside one segment: files.path is a "/"-separated key, so
    # such a segment names no object. The pre-web3 tail capture arrived joined,
    # which made this the same request as /dav/docs/notes.txt and let two URLs
    # name one file; it is a 400 now.
    case("propfind.encoded.slash", "PROPFIND", "/dav/docs%2Fnotes.txt", {"Depth": "0"})
    # the subdomain vhost sends X-Dav-Prefix: / so hrefs come back as /foo.txt
    # rather than /dav/foo.txt (otherwise dav.dawnop.com serves /dav/dav/...)
    case(
        "propfind.prefix",
        "PROPFIND",
        "/dav/",
        {"Depth": "1", "X-Dav-Prefix": "/"},
        with_facts=True,
    )

    print("\n== PROPFIND Depth fails closed ==")
    # RFC 4918 gives PROPFIND 0, 1 and infinity; this server does two of them.
    # A Depth it cannot honour used to be read as Depth 1, because the header was
    # only ever compared against "0" — so "infinity" listed one level and said
    # nothing about the rest, and a typo listed a directory the client had asked
    # not to walk. Repetition has no single value in any order and is refused
    # too. The valid readings are pinned in the same case, so a fix that closed
    # the door on everything would be just as red.
    depth_probe = {}
    for label, values in (
        ("missing", []),
        ("zero", ["0"]),
        ("one", ["1"]),
        ("infinity", ["infinity"]),
        ("infinity-uppercase", ["INFINITY"]),
        ("garbage", ["garbage"]),
        ("empty", [""]),
        ("two", ["2"]),
        ("comma-list", ["0,1"]),
        ("repeat-zero-one", ["0", "1"]),
        ("repeat-one-zero", ["1", "0"]),
        ("repeat-identical", ["1", "1"]),
        ("repeat-with-infinity", ["0", "infinity"]),
    ):
        head, raw = raw_http(
            B,
            "PROPFIND",
            "/dav/docs",
            [
                ("Authorization", "Basic " + base64.b64encode(auth.encode()).decode()),
                *(("Depth", v) for v in values),
            ],
        )
        report = wire_report(head, raw)
        body = normalize(raw.decode("utf-8", "replace"))
        entry = {
            "status_line": report["status_line"],
            "content_type_values": report["content_type_values"],
            "malformed_header_lines": report["malformed_header_lines"],
            "responses": body.count("<D:response>"),
        }
        if not report["status_line"].startswith("HTTP/1.1 207"):
            entry["body"] = body
        depth_probe[label] = entry
    g.case("propfind.depth.invalid.fail-closed", depth_probe)

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
    # An encoded slash in a Destination segment, the rule the request path
    # already enforces (see propfind.encoded.slash). Decoding the whole path
    # before splitting it made /dav/a%2Fb/c and /dav/a/b/c one target, so a MOVE
    # could land where its URL did not say it would. Both verbs read the header
    # through the same parser, so both refuse; the source exists in both, which
    # is what puts the answer past the 404 check and onto the Destination.
    case(
        "move.dest.encoded.slash",
        "MOVE",
        f"/dav/{PREFIX}/sub",
        {"Destination": f"/dav/{PREFIX}/a%2Fb"},
    )
    case(
        "copy.dest.encoded.slash",
        "COPY",
        f"/dav/{PREFIX}/sub",
        {"Destination": f"/dav/{PREFIX}/a%2Fb"},
    )
    case(
        "move.dest.dot",
        "MOVE",
        f"/dav/{PREFIX}/sub",
        {"Destination": "/dav/."},
    )
    case(
        "propfind.move.dot.source",
        "PROPFIND",
        f"/dav/{PREFIX}/sub",
        {"Depth": "0"},
        with_facts=True,
    )
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
        "copy.dest.encoded.dotdot",
        "COPY",
        "/dav/empty-dir",
        {"Destination": "/dav/%2E%2E"},
    )
    case(
        "propfind.copy.dotdot.source",
        "PROPFIND",
        "/dav/empty-dir",
        {"Depth": "0"},
        with_facts=True,
    )

    print("\n== Destination URI validation ==")
    case(
        "copy.dest.scheme.unsupported",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"ftp://{authority}/dav/{PREFIX}/rejected-scheme"},
    )
    case(
        "propfind.dest.scheme.rejected",
        "PROPFIND",
        f"/dav/{PREFIX}/rejected-scheme",
        {"Depth": "0"},
    )
    case(
        "copy.dest.authority.foreign",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"https://foreign.invalid/dav/{PREFIX}/rejected-authority"},
    )
    case(
        "propfind.dest.authority.rejected",
        "PROPFIND",
        f"/dav/{PREFIX}/rejected-authority",
        {"Depth": "0"},
    )
    case(
        "copy.dest.host.malformed",
        "COPY",
        "/dav/empty-dir",
        {
            "Host": "[bad",
            "Destination": f"http://{authority}/dav/{PREFIX}/rejected-host",
        },
    )
    case(
        "propfind.dest.host.rejected",
        "PROPFIND",
        f"/dav/{PREFIX}/rejected-host",
        {"Depth": "0"},
    )
    case(
        "copy.dest.prefix.partial",
        "COPY",
        "/dav/empty-dir",
        {"Destination": "/davx"},
    )
    case("propfind.dest.prefix.partial", "PROPFIND", "/dav/x", {"Depth": "0"})
    case(
        "copy.dest.prefix.outside",
        "COPY",
        "/dav/empty-dir",
        {"Destination": "/outside"},
    )
    case("propfind.dest.prefix.outside", "PROPFIND", "/dav/outside", {"Depth": "0"})
    case(
        "copy.dest.prefix.reserved-slash",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/d%2Fav/{PREFIX}/reserved-prefix"},
    )
    case(
        "propfind.dest.prefix.reserved-slash",
        "PROPFIND",
        f"/dav/{PREFIX}/reserved-prefix",
        {"Depth": "0"},
    )
    case(
        "copy.dest.malformed",
        "COPY",
        "/dav/empty-dir",
        {"Destination": "http://[bad"},
    )
    case(
        "copy.dest.opaque",
        "COPY",
        "/dav/empty-dir",
        {"Destination": "urn:dawnop:target"},
    )
    case(
        "copy.dest.query",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav/{PREFIX}/rejected-query?rev=1"},
    )
    case(
        "copy.dest.query.empty",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav/{PREFIX}/rejected-query?"},
    )
    case(
        "propfind.dest.query.rejected",
        "PROPFIND",
        f"/dav/{PREFIX}/rejected-query",
        {"Depth": "0"},
    )
    case(
        "copy.dest.fragment",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav/{PREFIX}/rejected-fragment#part"},
    )
    case(
        "copy.dest.fragment.empty",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav/{PREFIX}/rejected-fragment#"},
    )
    case(
        "propfind.dest.fragment.rejected",
        "PROPFIND",
        f"/dav/{PREFIX}/rejected-fragment",
        {"Depth": "0"},
    )
    case(
        "copy.dest.utf8.invalid-ff",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav/{PREFIX}/invalid-%FF"},
    )
    case(
        "copy.dest.utf8.invalid-fe",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav/{PREFIX}/invalid-%FE"},
    )
    case(
        "propfind.dest.utf8.no-replacement-alias",
        "PROPFIND",
        f"/dav/{PREFIX}/invalid-%EF%BF%BD",
        {"Depth": "0"},
    )
    case(
        "copy.dest.file-parent",
        "COPY",
        "/dav/empty-dir",
        {"Destination": "/dav/example.txt/child"},
    )
    case(
        "propfind.dest.file-parent.rejected",
        "PROPFIND",
        "/dav/example.txt/child",
        {"Depth": "0"},
    )

    print("\n== Destination URI legal controls ==")
    case(
        "copy.dest.path-absolute",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav/{PREFIX}/path-absolute"},
    )
    case(
        "propfind.dest.path-absolute",
        "PROPFIND",
        f"/dav/{PREFIX}/path-absolute",
        {"Depth": "0"},
        with_facts=True,
    )
    case(
        "copy.dest.absolute-http",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"{B}/dav/{PREFIX}/absolute-http"},
    )
    case(
        "propfind.dest.absolute-http",
        "PROPFIND",
        f"/dav/{PREFIX}/absolute-http",
        {"Depth": "0"},
        with_facts=True,
    )
    case(
        "copy.dest.absolute-https",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"https://{authority}/dav/{PREFIX}/absolute-https"},
    )
    case(
        "propfind.dest.absolute-https",
        "PROPFIND",
        f"/dav/{PREFIX}/absolute-https",
        {"Depth": "0"},
        with_facts=True,
    )
    case(
        "copy.dest.prefix-unreserved.path",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/d%61v/{PREFIX}/prefix-unreserved-path"},
    )
    case(
        "propfind.dest.prefix-unreserved.path",
        "PROPFIND",
        f"/dav/{PREFIX}/prefix-unreserved-path",
        {"Depth": "0"},
        with_facts=True,
    )
    case(
        "copy.dest.prefix-unreserved.absolute",
        "COPY",
        "/dav/empty-dir",
        {
            "Destination": f"http://{authority}/d%61v/{PREFIX}/prefix-unreserved-absolute"
        },
    )
    case(
        "propfind.dest.prefix-unreserved.absolute",
        "PROPFIND",
        f"/dav/{PREFIX}/prefix-unreserved-absolute",
        {"Depth": "0"},
        with_facts=True,
    )
    case(
        "copy.dest.authority-normalized",
        "COPY",
        "/dav/empty-dir",
        {
            "Host": "DAV.EXAMPLE",
            "Destination": f"HTTP://dav.example:80/dav/{PREFIX}/authority-normalized",
        },
    )
    case(
        "propfind.dest.authority-normalized",
        "PROPFIND",
        f"/dav/{PREFIX}/authority-normalized",
        {"Depth": "0"},
        with_facts=True,
    )
    case(
        "copy.dest.double-slash",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav//{PREFIX}//double-slash"},
    )
    case(
        "propfind.dest.double-slash",
        "PROPFIND",
        f"/dav/{PREFIX}/double-slash",
        {"Depth": "0"},
        with_facts=True,
    )
    case(
        "copy.dest.trailing-slash",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav/{PREFIX}/trailing-slash/"},
    )
    case(
        "propfind.dest.trailing-slash",
        "PROPFIND",
        f"/dav/{PREFIX}/trailing-slash",
        {"Depth": "0"},
        with_facts=True,
    )
    case(
        "copy.dest.root-prefix",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/{PREFIX}/root-prefix", "X-Dav-Prefix": "/"},
    )
    case(
        "propfind.dest.root-prefix",
        "PROPFIND",
        f"/dav/{PREFIX}/root-prefix",
        {"Depth": "0"},
        with_facts=True,
    )
    case(
        "copy.dest.encoded-question",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav/{PREFIX}/question%3Fname"},
    )
    case(
        "propfind.dest.encoded-question",
        "PROPFIND",
        f"/dav/{PREFIX}/question%3Fname",
        {"Depth": "0"},
        with_facts=True,
    )
    case(
        "copy.dest.encoded-fragment",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav/{PREFIX}/fragment%23name"},
    )
    case(
        "propfind.dest.encoded-fragment",
        "PROPFIND",
        f"/dav/{PREFIX}/fragment%23name",
        {"Depth": "0"},
        with_facts=True,
    )
    case(
        "copy.dest.utf8.multibyte",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav/{PREFIX}/%E5%90%88%E6%B3%95"},
    )
    case(
        "propfind.dest.utf8.multibyte",
        "PROPFIND",
        f"/dav/{PREFIX}/%E5%90%88%E6%B3%95",
        {"Depth": "0"},
        with_facts=True,
    )
    case(
        "copy.dest.utf8.replacement-character",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav/{PREFIX}/valid-%EF%BF%BD"},
    )
    case(
        "propfind.dest.utf8.replacement-character",
        "PROPFIND",
        f"/dav/{PREFIX}/valid-%EF%BF%BD",
        {"Depth": "0"},
        with_facts=True,
    )
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
    case(
        "copy.dest.hidden",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav/{PREFIX}/.hidden"},
    )
    case(
        "propfind.dest.hidden",
        "PROPFIND",
        f"/dav/{PREFIX}/.hidden",
        {"Depth": "0"},
        with_facts=True,
    )
    # Refusing encoded slashes and exact dot segments must not disable decoding
    # generally. %2E inside a name is a dot, so the collection lands at dot.name.
    # Without this, the refusals would pass if the parser stopped decoding.
    case(
        "copy.dest.encoded.dot",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav/{PREFIX}/dot%2Ename"},
    )
    case(
        "propfind.dest.encoded.dot",
        "PROPFIND",
        f"/dav/{PREFIX}/dot.name",
        {"Depth": "0"},
        with_facts=True,
    )
    # And a literal '+' stays a '+' rather than becoming a space: the decode is
    # Python's unquote, not a form decode. The href comes back as a%2Bb because
    # that is how '+' is written in a path once encoded again.
    case(
        "copy.dest.plus",
        "COPY",
        "/dav/empty-dir",
        {"Destination": f"/dav/{PREFIX}/a+b"},
    )
    case(
        "propfind.dest.plus",
        "PROPFIND",
        f"/dav/{PREFIX}/a+b",
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
    # recording — only a note that this is where THIS script's coverage stops.
    # Each one is a real case in contract_qiniu.py, against the fake bucket.
    covered = "covered in contract_qiniu.py (fake bucket); unconfigured here"
    g.skip("get.file", f"GET streams object bytes — {covered}")
    g.skip("put.new", f"PUT uploads object bytes — {covered}")
    g.skip("copy.file", f"COPY of a file duplicates the object — {covered}")
    g.skip("delete.file", f"DELETE of a file removes the object — {covered}")

    return g.finish()


if __name__ == "__main__":
    sys.exit(main())
