"""External loopback peer with deterministic bytes and observable requests."""

import argparse
import hashlib
import json
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

BLOCK = bytes(range(256)) * 256
HEAD = b"multipart-head\r\n\0"
TAIL = b"\r\n\0multipart-tail"


class Peer(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def reply(self, status, body):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def observe(self):
        target = self.raw_requestline.split(b" ")[1]
        with self.server.events.open("a") as out:
            out.write(
                json.dumps(
                    {
                        "method": self.command,
                        "target_hex": target.hex(),
                        "path": urlsplit(self.path).path,
                        "range": self.headers.get("Range"),
                        "auth": self.headers.get("Authorization")
                        == "Bearer transport-fixture-secret",
                        "poisoned": self.headers.get("X-Poison"),
                        "content_length": self.headers.get_all("Content-Length", []),
                        "transfer_encoding": self.headers.get_all(
                            "Transfer-Encoding", []
                        ),
                        "expect": self.headers.get_all("Expect", []),
                        "expected_length": self.headers.get("X-Expected-Length"),
                    }
                )
                + "\n"
            )

    def do_PUT(self):
        self.do_POST()

    def early_refusal(self):
        # A complete early response followed by a bounded graceful close.
        # Closing with unread incoming bytes can reset TCP and erase the
        # response at the client before it has observed the 401.
        self.send_response(401)
        self.send_header("Content-Length", "13")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b"early refusal")
        self.wfile.flush()
        self.connection.shutdown(socket.SHUT_WR)
        deadline = time.monotonic() + 2
        left = 8 * 1024 * 1024
        try:
            while left and time.monotonic() < deadline:
                self.connection.settimeout(max(0.001, deadline - time.monotonic()))
                chunk = self.rfile.read1(min(left, 65536))
                if not chunk:
                    break
                left -= len(chunk)
            if left == 0:
                # Keep the read side alive long enough to observe the response
                # even if an uploading client fills the bounded drain budget.
                threading.Event().wait(max(0, deadline - time.monotonic()))
        except OSError:
            pass
        self.close_connection = True

    def do_POST(self):
        self.observe()
        wanted = self.headers.get("X-Expected-Length")
        if wanted is not None and (
            self.headers.get_all("Content-Length", []) != [wanted]
            or self.headers.get_all("Transfer-Encoding", [])
            or self.headers.get_all("Expect", [])
        ):
            self.reply(400, b"upload framing mismatch")
            self.close_connection = True
            return
        path = urlsplit(self.path).path
        if path == "/early":
            self.early_refusal()
            return
        digest = hashlib.sha256()
        left = int(self.headers.get("Content-Length", "0"))
        count = left
        while left:
            chunk = self.rfile.read(min(left, 65536))
            if not chunk:
                break
            digest.update(chunk)
            left -= len(chunk)
            if path == "/slow-upload":
                time.sleep(0.1)
        self.reply(201, f"{count - left}:{digest.hexdigest()}".encode())

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)
        self.observe()
        if path == "/peer-close":
            self.close_connection = True
            return
        elif path == "/empty":
            self.reply(204, b"")
        elif path == "/quiet":
            time.sleep(2.5)
            self.reply(200, b"late")
        elif path == "/binary":
            self.reply(200, BLOCK)
        elif path == "/range":
            if self.headers.get("Range") == "bytes=7-263":
                self.reply(206, BLOCK[7:264])
            else:
                self.reply(200, BLOCK)
        elif path in ("/denied", "/error"):
            self.reply(401 if path == "/denied" else 503, b"upstream refusal\0tail")
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/binary")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/short":
            self.send_response(200)
            self.send_header("Content-Length", "1234")
            self.end_headers()
            self.wfile.write(b"short\0body")
            self.wfile.flush()
            self.close_connection = True
        elif path == "/informational":
            self.wfile.write(b"HTTP/1.1 103 Early Hints\r\nLink: </x>\r\n\r\n")
            self.reply(201, b"final response")
        elif path == "/chunked":
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(
                b"4\r\na\x00\xffz\r\n3\r\nend\r\n0\r\nX-Trailer: final\r\n\r\n"
            )
        elif path in ("/large", "/slow", "/oversize", "/oversize-chunked"):
            count = int(q.get("bytes", ["8388608"])[0])
            chunked = path == "/oversize-chunked"
            self.send_response(200)
            self.send_header(
                "Transfer-Encoding" if chunked else "Content-Length",
                "chunked" if chunked else str(count),
            )
            self.end_headers()
            if self.command == "HEAD":
                return
            left = count
            while left:
                chunk = BLOCK[: min(left, len(BLOCK))]
                if path.startswith("/oversize"):
                    chunk = b"x" * len(chunk)
                if chunked:
                    self.wfile.write(f"{len(chunk):x}\r\n".encode())
                self.wfile.write(chunk)
                if chunked:
                    self.wfile.write(b"\r\n")
                self.wfile.flush()
                left -= len(chunk)
                if path == "/slow":
                    time.sleep(0.0125)
            if chunked:
                self.wfile.write(b"0\r\n\r\n")
        else:
            self.reply(200, b"management text\0tail")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, *args):
        # A timeout, size limit, refusal or disconnected consumer is expected.
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--cert", type=Path)
    parser.add_argument("--key", type=Path)
    args = parser.parse_args()
    server = Server(("127.0.0.1", 0), Peer)
    server.events = args.events
    if args.cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(args.cert, args.key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
    args.ready.write_text(str(server.server_port))
    # Readiness is event driven through the inherited stdout pipe.
    print(server.server_port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
