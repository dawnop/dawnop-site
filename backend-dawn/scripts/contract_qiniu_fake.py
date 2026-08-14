#!/usr/bin/env python3
"""A fake qiniu Kodo bucket, on loopback, for the contract harness.

Why this exists
---------------
Four WebDAV methods and two fm endpoints reach the object store, so with no
credentials they could only ever be named skips — and the paths behind them are
not incidental: a COPY of a *subtree* is the only place the backend issues one
qiniu call per file row, and the mapping "object store refused -> 502" had
already regressed once to a 500 (see errkind.dawn's header) with nothing but
review standing in the way.

This serves qiniu's documented wire protocol for exactly what the backend uses:

    POST /copy/<srcEntry>/<dstEntry>/force/true   (bucket manager)
    POST /delete/<entry>
    GET  /stat/<entry>
    POST /                                        (upload, multipart form)
    GET  /<key>?e=<deadline>&token=<ak>:<sign>    (private download, Range-aware)

It is a *protocol* fake, not a stub: every request's signature is recomputed
with HMAC-SHA1 and rejected if it does not match, so qiniu_sign.dawn's tokens
are exercised end to end rather than asserted against themselves. An upload
token's putPolicy deadline is held to the same standard: it must still be in
date and inside a day, so a token that cannot be spent cannot pass. What it does
not model — regions, quotas, rate limits, real 612/631 taxonomy beyond the two
codes the backend branches on — is exactly the part a golden could not pin
against the live bucket either.

The credentials below are literals invented for this file. They authenticate
nothing anywhere: the "bucket" is a dict in this process and the "host" is
127.0.0.1. Nothing real is signed with them.

Control plane (not qiniu; only the contract script calls it):

    GET  /__fake/calls          -> the ops seen since the last reset
    POST /__fake/reset          -> reseed objects, clear calls and refusals
    POST /__fake/refuse         -> {"op","status","body"}; status 0 disarms
    POST /__fake/put            -> {"key","content","mime"}; plant an object
    POST /__fake/pause          -> {"op"}; pause matching object requests
    POST /__fake/stall          -> {"op","seconds"}; go quiet for that long
    POST /__fake/release        -> {"op"}; release and disarm that pause
    GET  /__fake/pauses         -> entered request counts for armed pauses

Standalone (the harness starts it in-process instead):

    python3 contract_qiniu_fake.py --port 18100
"""

import argparse
import base64
import hashlib
import hmac
import json
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import contract_fixture

# Invented for this file; see the module docstring. Spelled without the words a
# secret scanner keys its generic rules on, for the same reason
# contract_run.CONTRACT_JWT_PHRASE is.
FAKE_AK = "contract-fake-ak"
FAKE_SK = "contract-fake-sk"
FAKE_BUCKET = "contract-fake-bucket"

# The multipart boundary qiniu_rs.dawn frames its upload bodies with.
BOUNDARY = "----DawnFmBoundary7MA4YWxkTrZu0gW"


def _sign(payload: str) -> str:
    """HMAC-SHA1, urlsafe base64 with padding — qiniu's signature encoding."""
    mac = hmac.new(FAKE_SK.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.urlsafe_b64encode(mac).decode()


def _entry(encoded: str) -> tuple:
    """Decode an EncodedEntryURI back into (bucket, key)."""
    pad = "=" * (-len(encoded) % 4)
    raw = base64.urlsafe_b64decode(encoded + pad).decode("utf-8")
    bucket, _, key = raw.partition(":")
    return bucket, key


def seed_objects() -> dict:
    """The fixture's file rows, as objects with bodies of the recorded length.

    The contents are derived from the key so a GET is reproducible, and padded
    to the size the fixture row claims so `stat` agrees with the database — the
    disagreement between them is a thing /register exists to catch, and a fake
    that could not represent it would make that case vacuous.
    """
    objs = {}
    for _id, path, is_dir, key, ctype, size, _day in contract_fixture.FILES:
        if is_dir or not key:
            continue
        unit = f"fixture object for {path}\n".encode()
        body = (unit * (size // len(unit) + 1))[:size]
        objs[key] = {"content": body, "mime": ctype}
    return objs


class Bucket:
    """The mutable state: objects, the call log, and any armed refusal."""

    def __init__(self, public_base: str):
        self.public_base = public_base
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            for gate in getattr(self, "pauses", {}).values():
                gate.set()
            self.objects = seed_objects()
            self.calls = []
            self.refuse = {}
            self.pauses = {}
            self.pause_entered = {}
            self.stalls = {}

    def log(self, **kw):
        with self.lock:
            self.calls.append(kw)

    def arm_stall(self, op: str, seconds: float):
        """Go quiet on `op` for `seconds` -- an endpoint that accepts and then
        says nothing, which is what the backend's request deadline exists for.
        Distinct from arm_pause: a pause waits to be released by the test, a
        stall runs out on its own so nothing can wedge if the deadline is gone.
        """
        with self.lock:
            if seconds > 0:
                self.stalls[op] = seconds
            else:
                self.stalls.pop(op, None)

    def stall_if_armed(self, op: str):
        with self.lock:
            seconds = self.stalls.get(op)
        if seconds:
            time.sleep(seconds)

    def arm_pause(self, op: str):
        with self.lock:
            old = self.pauses.pop(op, None)
            if old is not None:
                old.set()
            self.pauses[op] = threading.Event()
            self.pause_entered[op] = 0

    def wait_if_paused(self, op: str):
        with self.lock:
            gate = self.pauses.get(op)
            if gate is None:
                return
            self.pause_entered[op] = self.pause_entered.get(op, 0) + 1
        gate.wait(timeout=10)

    def release_pause(self, op: str):
        with self.lock:
            gate = self.pauses.pop(op, None)
        if gate is not None:
            gate.set()

    def pause_state(self):
        with self.lock:
            return {
                op: {"entered": self.pause_entered.get(op, 0)} for op in self.pauses
            }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    bucket: Bucket = None  # set by serve()

    # -- plumbing -------------------------------------------------------------

    def log_message(self, fmt, *args):  # noqa: A003 - silence the default stderr spam
        pass

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _send(self, status: int, body: bytes, mime="application/json", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload):
        self._send(status, json.dumps(payload).encode())

    def _qiniu_error(self, status: int, message: str):
        """qiniu's error shape: a JSON body with `error`, on a non-200 status."""
        self._json(status, {"error": message})

    # -- authentication -------------------------------------------------------

    def _check_qbox(self, signing_str: str) -> bool:
        """Management token: `QBox <ak>:<sign>` over "<path>\\n"."""
        got = self.headers.get("Authorization", "")
        return got == f"QBox {FAKE_AK}:{_sign(signing_str)}"

    def _refused(self, op: str, **detail) -> bool:
        """Answer with the armed refusal, if any. The attempt is still logged
        with its keys — which object the walk gave up on is the interesting part
        of a failed subtree copy."""
        rule = self.bucket.refuse.get(op)
        if not rule:
            return False
        self.bucket.log(op=op, outcome="refused", status=rule["status"], **detail)
        self._send(rule["status"], rule["body"].encode())
        return True

    # -- routing --------------------------------------------------------------

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's naming
        path, _, query = self.path.partition("?")
        segs = [s for s in path.split("/") if s]
        if segs[:1] == ["__fake"]:
            return self._control_get(segs)
        if segs[:1] == ["stat"] and len(segs) == 2:
            return self._stat(segs[1])
        if len(segs) == 1:
            return self._download(segs[0], query)
        return self._qiniu_error(404, "no such route")

    do_HEAD = do_GET

    def do_POST(self):  # noqa: N802
        path = self.path.partition("?")[0]
        segs = [s for s in path.split("/") if s]
        body = self._read_body()
        if segs[:1] == ["__fake"]:
            return self._control_post(segs, body)
        if not segs:
            return self._upload(body)
        if segs[0] == "delete" and len(segs) == 2:
            return self._delete(segs[1])
        if segs[0] == "copy" and len(segs) == 5 and segs[3:] == ["force", "true"]:
            return self._copy(segs[1], segs[2])
        return self._qiniu_error(404, "no such route")

    # -- qiniu: bucket manager ------------------------------------------------

    def _stat(self, encoded: str):
        if not self._check_qbox(f"/stat/{encoded}\n"):
            return self._qiniu_error(401, "bad token")
        bucket, key = _entry(encoded)
        self.bucket.log(op="stat", key=key)
        # logged before stalling, so the call log is the same whether or not the
        # caller waited around for an answer
        self.bucket.stall_if_armed("stat")
        if bucket != FAKE_BUCKET:
            return self._qiniu_error(631, "no such bucket")
        obj = self.bucket.objects.get(key)
        if obj is None:
            return self._qiniu_error(612, "no such file or directory")
        return self._json(
            200,
            {
                "fsize": len(obj["content"]),
                "hash": hashlib.sha1(obj["content"]).hexdigest(),
                "mimeType": obj["mime"],
                "putTime": 17600000000000000,
                "type": 0,
            },
        )

    def _delete(self, encoded: str):
        if not self._check_qbox(f"/delete/{encoded}\n"):
            return self._qiniu_error(401, "bad token")
        _bucket, key = _entry(encoded)
        self.bucket.wait_if_paused("delete")
        if self._refused("delete", key=key):
            return None
        existed = self.bucket.objects.pop(key, None) is not None
        self.bucket.log(op="delete", key=key, existed=existed)
        if not existed:
            return self._qiniu_error(612, "no such file or directory")
        return self._send(200, b"")

    def _copy(self, src_enc: str, dst_enc: str):
        if not self._check_qbox(f"/copy/{src_enc}/{dst_enc}/force/true\n"):
            return self._qiniu_error(401, "bad token")
        _sb, src = _entry(src_enc)
        _db, dst = _entry(dst_enc)
        if self._refused("copy", src=src, dst=dst):
            return None
        obj = self.bucket.objects.get(src)
        self.bucket.log(op="copy", src=src, dst=dst, found=obj is not None)
        if obj is None:
            return self._qiniu_error(612, "no such file or directory")
        self.bucket.objects[dst] = dict(obj)
        return self._send(200, b"")

    # -- qiniu: upload --------------------------------------------------------

    def _upload(self, body: bytes):
        parts = _multipart(body)
        token = parts.get("token", (b"", ""))[0].decode()
        key = parts.get("key", (b"", ""))[0].decode()
        if "file" not in parts:
            return self._qiniu_error(400, "no file part")
        content, mime = parts["file"]
        if not _check_upload_token(token, key):
            return self._qiniu_error(401, "bad token")
        self.bucket.wait_if_paused("upload")
        if self._refused("upload", key=key):
            return None
        self.bucket.objects[key] = {"content": content, "mime": mime}
        self.bucket.log(op="upload", key=key, size=len(content), mime=mime)
        return self._json(200, {"key": key, "hash": hashlib.sha1(content).hexdigest()})

    # -- qiniu: private download ----------------------------------------------

    def _download(self, key: str, query: str):
        key = urllib.parse.unquote(key)
        q = urllib.parse.parse_qs(query, keep_blank_values=True)
        deadline = (q.get("e") or [""])[0]
        token = (q.get("token") or [""])[0]
        # The signed string is the whole URL up to and including ?e=<deadline>,
        # which is why the fake has to know the base the backend was configured
        # with: a mismatch here means the download URL was assembled wrong.
        signed = f"{self.bucket.public_base}/{key}?e={deadline}"
        if token != f"{FAKE_AK}:{_sign(signed)}":
            self.bucket.log(op="download", key=key, outcome="bad-signature")
            return self._qiniu_error(401, "bad token")
        obj = self.bucket.objects.get(key)
        if obj is None:
            self.bucket.log(op="download", key=key, outcome="missing")
            return self._qiniu_error(612, "no such file or directory")
        content = obj["content"]
        rng = _range(self.headers.get("Range", ""), len(content))
        if rng is None:
            self.bucket.log(op="download", key=key, outcome="full")
            return self._send(200, content, obj["mime"] or "application/octet-stream")
        start, end = rng
        self.bucket.log(op="download", key=key, outcome=f"range {start}-{end}")
        return self._send(
            206,
            content[start : end + 1],
            obj["mime"] or "application/octet-stream",
            {"Content-Range": f"bytes {start}-{end}/{len(content)}"},
        )

    # -- control plane --------------------------------------------------------

    def _control_get(self, segs):
        if segs[1:] == ["calls"]:
            return self._json(200, {"calls": self.bucket.calls})
        if segs[1:] == ["objects"]:
            return self._json(200, {"keys": sorted(self.bucket.objects)})
        if segs[1:] == ["state"]:
            with self.bucket.lock:
                state = {
                    key: {
                        "content": base64.b64encode(value["content"]).decode(),
                        "mime": value["mime"],
                    }
                    for key, value in self.bucket.objects.items()
                }
            return self._json(200, {"objects": state})
        if segs[1:] == ["pauses"]:
            return self._json(200, {"pauses": self.bucket.pause_state()})
        return self._json(404, {"error": "no such control endpoint"})

    def _control_post(self, segs, body: bytes):
        payload = json.loads(body or b"{}")
        if segs[1:] == ["reset"]:
            self.bucket.reset()
            return self._json(200, {"ok": True})
        if segs[1:] == ["calls"]:  # clear the log, keep objects and refusals
            self.bucket.calls = []
            return self._json(200, {"ok": True})
        if segs[1:] == ["refuse"]:
            op, status = payload["op"], int(payload["status"])
            if status:
                self.bucket.refuse[op] = {
                    "status": status,
                    "body": payload.get("body", '{"error":"refused by fake"}'),
                }
            else:
                self.bucket.refuse.pop(op, None)
            return self._json(200, {"ok": True, "refuse": sorted(self.bucket.refuse)})
        if segs[1:] == ["put"]:
            self.bucket.objects[payload["key"]] = {
                "content": payload.get("content", "").encode(),
                "mime": payload.get("mime", "application/octet-stream"),
            }
            return self._json(200, {"ok": True})
        if segs[1:] == ["stall"]:
            self.bucket.arm_stall(payload["op"], float(payload.get("seconds", 0)))
            return self._json(200, {"ok": True})
        if segs[1:] == ["pause"]:
            self.bucket.arm_pause(payload["op"])
            return self._json(200, {"ok": True})
        if segs[1:] == ["release"]:
            self.bucket.release_pause(payload["op"])
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "no such control endpoint"})


def _multipart(body: bytes) -> dict:
    """name -> (value bytes, part content-type), for qiniu's upload form."""
    sep = b"--" + BOUNDARY.encode()
    out = {}
    for part in body.split(sep)[1:]:
        if part[:2] == b"--":  # closing delimiter
            break
        if part[:2] == b"\r\n":
            part = part[2:]
        head, _, value = part.partition(b"\r\n\r\n")
        if value.endswith(b"\r\n"):
            value = value[:-2]
        name = re.search(rb'name="([^"]*)"', head)
        ctype = re.search(rb"Content-Type:\s*([^\r\n]+)", head)
        if name:
            out[name.group(1).decode()] = (
                value,
                ctype.group(1).decode() if ctype else "",
            )
    return out


# The longest putPolicy lifetime this bucket will honour. qiniu's own ceiling is
# larger; a day is the one this deployment states (QINIU_TOKEN_EXPIRES is bounded
# to 1..86400), and a fake that accepted any deadline could not tell a token
# that works from a token that merely parses.
MAX_TOKEN_LIFETIME = 86400


def _check_upload_token(token: str, key: str) -> bool:
    """`<ak>:<sign>:<encodedPutPolicy>`, scoped to this key and still in date.

    The deadline is checked, not just carried: a real bucket refuses an expired
    putPolicy, and without that the signing code could hand out tokens nobody
    could spend and every upload case here would still be green.
    """
    try:
        ak, sign, policy_b64 = token.split(":")
    except ValueError:
        return False
    if ak != FAKE_AK or sign != _sign(policy_b64):
        return False
    pad = "=" * (-len(policy_b64) % 4)
    policy = json.loads(base64.urlsafe_b64decode(policy_b64 + pad))
    if policy.get("scope") != f"{FAKE_BUCKET}:{key}":
        return False
    deadline = policy.get("deadline")
    if not isinstance(deadline, int) or isinstance(deadline, bool):
        return False
    now = int(time.time())
    return now < deadline <= now + MAX_TOKEN_LIFETIME


def _range(header: str, total: int):
    """`bytes=a-b` -> inclusive (start, end), or None for anything else."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not m or total <= 0:
        return None
    start, end = m.group(1), m.group(2)
    if start == "":
        if end == "":
            return None
        return max(0, total - int(end)), total - 1
    s = int(start)
    e = total - 1 if end == "" else min(int(end), total - 1)
    return (s, e) if s <= e else None


def serve(port: int) -> ThreadingHTTPServer:
    """Start the fake on `port` in a daemon thread; returns the server."""
    base = f"http://127.0.0.1:{port}"
    handler = type("BoundHandler", (Handler,), {"bucket": Bucket(base)})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18100)
    args = ap.parse_args()
    httpd = serve(args.port)
    print(f"fake qiniu on http://127.0.0.1:{args.port} (ctrl-c to stop)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
