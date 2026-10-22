"""A loopback object peer that hashes uploads and streams downloads from disk."""

import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Peer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path):
        super().__init__(("127.0.0.1", 0), Handler)
        self.root = root
        self.last_upload = None
        self.payload = None
        self.errors = []
        self.upload_bytes = 0
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server_port}"

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=5)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def reply(self, status, body=b"{}"):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        self.close_connection = True

    def do_POST(self):
        # Every qiniu file upload has two bounded fields then one streamed file.
        if self.path != "/":
            self.reply(200)
            return
        remaining = int(self.headers["Content-Length"])
        prefix = self.rfile.read(min(4096, remaining))
        self.server.upload_bytes += len(prefix)
        remaining -= len(prefix)
        marker = prefix.index(b'name="file"')
        start = prefix.index(b"\r\n\r\n", marker) + 4
        boundary = self.headers["Content-Type"].split("boundary=", 1)[1].encode()
        tail = b"\r\n--" + boundary + b"--\r\n"
        pending = prefix[start:]
        digest = hashlib.sha256()
        size = 0
        while remaining:
            chunk = self.rfile.read(min(65536, remaining))
            self.server.upload_bytes += len(chunk)
            if not chunk:
                self.server.errors.append("short upstream upload")
                return
            remaining -= len(chunk)
            pending += chunk
            if len(pending) > len(tail):
                data, pending = pending[: -len(tail)], pending[-len(tail) :]
                digest.update(data)
                size += len(data)
        if len(pending) > len(tail):
            data, pending = pending[: -len(tail)], pending[-len(tail) :]
            digest.update(data)
            size += len(data)
        assert pending == tail
        key = re.search(rb'name="key"\r\n\r\n([^\r]+)', prefix)[1].decode()
        self.server.last_upload = (key, size, digest.hexdigest())
        self.reply(200, json.dumps({"key": key}).encode())

    def do_GET(self):
        if self.path.startswith("/stat/"):
            self.reply(200, b'{"fsize":1,"mimeType":"application/octet-stream"}')
            return
        path = self.server.payload
        if path is None:
            self.reply(404)
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        status = 200
        if self.headers.get("Range"):
            start, end = map(
                int, self.headers["Range"].removeprefix("bytes=").split("-")
            )
            status = 206
        self.send_response(status)
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            with path.open("rb") as stream:
                stream.seek(start)
                left = end - start + 1
                while left:
                    chunk = stream.read(min(left, 65536))
                    self.wfile.write(chunk)
                    left -= len(chunk)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True
