#!/usr/bin/env python3
"""WebDAV contract test: Dawn (:8001/dav) vs FastAPI (:8000/dav).

Both backends share the same SQLite DB + qiniu bucket, so a write on one is
visible to the other. This harness exploits that for cross-verification:

  * READ methods (OPTIONS, PROPFIND, HEAD, GET) are issued to *both* backends
    against the same path and diffed (headers / XML structure / bytes).
  * WRITE methods (PUT, MKCOL, MOVE, COPY, DELETE) are executed on ONE backend,
    then the *other* is asked to read the result back — proving the two agree
    on the shared path<->key representation in both directions.

Everything happens under an isolated `dav-test/` prefix and is cleaned up at
the end (and defensively at the start), so production files are never touched.

Auth is HTTP Basic (admin creds), passed via env so they never touch the file.

Usage (on the server, both ports localhost):
    DAV_USER=... DAV_PASS=... python3 contract_webdav.py \
        --dawn http://127.0.0.1:8001 --fast http://127.0.0.1:8000
"""

import argparse
import base64
import hashlib
import os
import re
import sys
import urllib.error
import urllib.request

PREFIX = "dav-test"  # isolated sandbox subtree

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
INFO = "\033[36m----\033[0m"

fails = 0


def dav(base, method, path, auth, headers=None, body=None, timeout=30):
    """One WebDAV request. Returns (status, resp_headers_dict, body_bytes)."""
    url = base + path
    hdrs = dict(headers or {})
    hdrs["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
    data = body if isinstance(body, (bytes, type(None))) else body.encode()
    r = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return (
                resp.status,
                {k.lower(): v for k, v in resp.headers.items()},
                resp.read(),
            )
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read()


def check(label, cond, detail=""):
    global fails
    mark = PASS if cond else FAIL
    if not cond:
        fails += 1
    print(f"  [{mark}] {label}" + (f"  {detail}" if detail and not cond else ""))
    return cond


def hrefs(xml):
    """Extract <d:href> texts (namespace-agnostic), normalized."""
    return sorted(
        re.findall(r"<(?:\w+:)?href>\s*(.*?)\s*</(?:\w+:)?href>", xml, re.I | re.S)
    )


def displaynames(xml):
    return sorted(
        re.findall(
            r"<(?:\w+:)?displayname>\s*(.*?)\s*</(?:\w+:)?displayname>",
            xml,
            re.I | re.S,
        )
    )


def cleanup(base, auth):
    dav(base, "DELETE", f"/dav/{PREFIX}", auth)
    dav(base, "DELETE", f"/dav/{PREFIX}/", auth)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dawn", required=True)
    ap.add_argument("--fast", required=True)
    args = ap.parse_args()
    user = os.environ["DAV_USER"]
    pw = os.environ["DAV_PASS"]
    auth = f"{user}:{pw}"
    D, F = args.dawn, args.fast

    # defensive pre-clean on both (either could hold a stale sandbox row)
    cleanup(D, auth)
    cleanup(F, auth)

    print(f"\n{INFO} 1. OPTIONS (read, both) — DAV class + Allow")
    for name, base in (("dawn", D), ("fast", F)):
        st, h, _ = dav(base, "OPTIONS", "/dav/", auth)
        dav_hdr = h.get("dav", "")
        allow = h.get("allow", "")
        print(f"    {name}: {st}  DAV='{dav_hdr}'  Allow='{allow}'")
    stD, hD, _ = dav(D, "OPTIONS", "/dav/", auth)
    stF, hF, _ = dav(F, "OPTIONS", "/dav/", auth)
    check("OPTIONS status 200 both", stD == 200 and stF == 200, f"{stD}/{stF}")
    check(
        "DAV header advertises class 1,2 (dawn)",
        "1" in hD.get("dav", "") and "2" in hD.get("dav", ""),
        hD.get("dav"),
    )
    check(
        "DAV header advertises class 1,2 (fast)",
        "1" in hF.get("dav", "") and "2" in hF.get("dav", ""),
        hF.get("dav"),
    )

    print(f"\n{INFO} 2. Auth: no creds -> 401 (both)")
    for name, base in (("dawn", D), ("fast", F)):
        url = base + "/dav/"
        r = urllib.request.Request(url, method="PROPFIND", headers={"Depth": "0"})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                st = resp.status
                wa = resp.headers.get("WWW-Authenticate", "")
        except urllib.error.HTTPError as e:
            st = e.code
            wa = (e.headers or {}).get("WWW-Authenticate", "")
        check(f"unauth PROPFIND 401 ({name})", st == 401, str(st))
        check(f"WWW-Authenticate: Basic ({name})", "Basic" in (wa or ""), wa)

    print(f"\n{INFO} 3. MKCOL on Dawn -> both see the dir via PROPFIND")
    st, _, _ = dav(D, "MKCOL", f"/dav/{PREFIX}", auth)
    check("MKCOL sandbox 201 (dawn)", st == 201, str(st))
    st, _, _ = dav(D, "MKCOL", f"/dav/{PREFIX}/sub", auth)
    check("MKCOL sub 201 (dawn)", st == 201, str(st))
    # FastAPI must now list the sub dir
    st, _, body = dav(F, "PROPFIND", f"/dav/{PREFIX}", auth, {"Depth": "1"})
    hf = hrefs(body.decode("utf-8", "replace"))
    check("PROPFIND on fast sees dawn-made dir 200", st == 207, str(st))
    check("fast lists /dav/dav-test/sub", any("sub" in h for h in hf), str(hf))

    print(f"\n{INFO} 4. PUT binary on Dawn -> GET byte-exact on BOTH")
    # all-byte-values payload to stress the latin-1 round trip
    payload = bytes(range(256)) * 8  # 2048 bytes, every byte value
    sha = hashlib.sha256(payload).hexdigest()
    st, _, _ = dav(
        D,
        "PUT",
        f"/dav/{PREFIX}/blob.bin",
        auth,
        {"Content-Type": "application/octet-stream"},
        payload,
    )
    check("PUT blob 201/204 (dawn)", st in (201, 204), str(st))
    for name, base in (("dawn", D), ("fast", F)):
        st, h, got = dav(base, "GET", f"/dav/{PREFIX}/blob.bin", auth)
        ok = st == 200 and hashlib.sha256(got).hexdigest() == sha
        check(
            f"GET blob byte-exact ({name})",
            ok,
            f"st={st} len={len(got)} sha={hashlib.sha256(got).hexdigest()[:12]} want={sha[:12]}",
        )

    print(f"\n{INFO} 5. PROPFIND file props parity (dawn vs fast, same path)")
    stD, _, bD = dav(D, "PROPFIND", f"/dav/{PREFIX}/blob.bin", auth, {"Depth": "0"})
    stF, _, bF = dav(F, "PROPFIND", f"/dav/{PREFIX}/blob.bin", auth, {"Depth": "0"})
    xD, xF = bD.decode("utf-8", "replace"), bF.decode("utf-8", "replace")
    check("PROPFIND file 207 both", stD == 207 and stF == 207, f"{stD}/{stF}")
    check("hrefs identical", hrefs(xD) == hrefs(xF), f"{hrefs(xD)} vs {hrefs(xF)}")
    # getcontentlength should both report 2048
    lenD = re.search(r"getcontentlength>\s*(\d+)", xD)
    lenF = re.search(r"getcontentlength>\s*(\d+)", xF)
    check(
        "getcontentlength both 2048",
        bool(lenD) and bool(lenF) and lenD.group(1) == "2048" == lenF.group(1),
        f"{lenD and lenD.group(1)} vs {lenF and lenF.group(1)}",
    )

    print(f"\n{INFO} 6. MOVE (rename) on Dawn -> fast sees new, not old")
    st, _, _ = dav(
        D,
        "MOVE",
        f"/dav/{PREFIX}/blob.bin",
        auth,
        {"Destination": f"/dav/{PREFIX}/moved.bin"},
    )
    check("MOVE 201/204 (dawn)", st in (201, 204), str(st))
    st, _, got = dav(F, "GET", f"/dav/{PREFIX}/moved.bin", auth)
    check(
        "fast GET moved.bin 200 + byte-exact",
        st == 200 and hashlib.sha256(got).hexdigest() == sha,
        str(st),
    )
    st, _, _ = dav(F, "GET", f"/dav/{PREFIX}/blob.bin", auth)
    check("fast GET old blob.bin 404", st == 404, str(st))

    print(f"\n{INFO} 7. COPY on Dawn -> both see two copies, same bytes")
    st, _, _ = dav(
        D,
        "COPY",
        f"/dav/{PREFIX}/moved.bin",
        auth,
        {"Destination": f"/dav/{PREFIX}/copy.bin"},
    )
    check("COPY 201/204 (dawn)", st in (201, 204), str(st))
    for name, base in (("dawn", D), ("fast", F)):
        s1, _, g1 = dav(base, "GET", f"/dav/{PREFIX}/moved.bin", auth)
        s2, _, g2 = dav(base, "GET", f"/dav/{PREFIX}/copy.bin", auth)
        ok = (
            s1 == 200
            and s2 == 200
            and hashlib.sha256(g1).hexdigest() == sha
            and hashlib.sha256(g2).hexdigest() == sha
        )
        check(f"both copies present + byte-exact ({name})", ok, f"{s1}/{s2}")

    print(f"\n{INFO} 8. LOCK/UNLOCK (fake lock) grant on Dawn")
    lockbody = (
        '<?xml version="1.0"?><d:lockinfo xmlns:d="DAV:">'
        "<d:lockscope><d:exclusive/></d:lockscope>"
        "<d:locktype><d:write/></d:locktype></d:lockinfo>"
    )
    st, h, body = dav(
        D,
        "LOCK",
        f"/dav/{PREFIX}/moved.bin",
        auth,
        {"Content-Type": "application/xml"},
        lockbody,
    )
    tok = h.get("lock-token", "")
    check("LOCK 200 (dawn)", st == 200, str(st))
    check(
        "Lock-Token issued",
        "opaquelocktoken" in (tok + body.decode("utf-8", "replace")),
        tok,
    )
    if tok:
        st, _, _ = dav(
            D, "UNLOCK", f"/dav/{PREFIX}/moved.bin", auth, {"Lock-Token": tok}
        )
        check("UNLOCK 204 (dawn)", st == 204, str(st))

    print(f"\n{INFO} 9. Cross-direction: PUT on FAST -> GET on Dawn")
    txt = "跨方向验证 cross-check ✓\n".encode("utf-8")
    st, _, _ = dav(
        F,
        "PUT",
        f"/dav/{PREFIX}/fromfast.txt",
        auth,
        {"Content-Type": "text/plain; charset=utf-8"},
        txt,
    )
    check("PUT fromfast (fast) 201/204", st in (201, 204), str(st))
    st, _, got = dav(D, "GET", f"/dav/{PREFIX}/fromfast.txt", auth)
    check(
        "dawn GET fromfast byte-exact",
        st == 200 and got == txt,
        f"st={st} {got[:20]!r}",
    )

    print(f"\n{INFO} 10. DELETE recursive on Dawn -> both see gone")
    st, _, _ = dav(D, "DELETE", f"/dav/{PREFIX}", auth)
    check("DELETE sandbox 204 (dawn)", st == 204, str(st))
    for name, base in (("dawn", D), ("fast", F)):
        st, _, _ = dav(base, "PROPFIND", f"/dav/{PREFIX}", auth, {"Depth": "0"})
        check(f"sandbox gone 404 ({name})", st == 404, str(st))

    # final defensive cleanup
    cleanup(D, auth)
    cleanup(F, auth)

    print(f"\n=== {'ALL PASS' if fails == 0 else str(fails) + ' FAIL'} ===")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
