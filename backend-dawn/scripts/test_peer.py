"""External loopback fixtures for the white box tests; no JDK server needed."""

import os
import select
import subprocess
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def serve(mode, status, body, delay):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/__stop":
                self.send_response(200)
                self.send_header("Content-Length", str(len(str(os.getpid()))))
                self.end_headers()
                self.wfile.write(str(os.getpid()).encode())
                self.wfile.flush()
                threading.Thread(target=self.server.shutdown).start()
                return
            if delay:
                threading.Event().wait(delay / 1000)
            reply = (
                str(self.client_address[1])
                if mode == "port"
                else body + (self.headers.get("X-Probe", "-") if mode == "echo" else "")
            ).encode()
            code = status
            if mode == "multipart":
                incoming = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                exact = incoming == Path(body).read_bytes()
                code, reply = (200, b"{}") if exact else (422, b"mismatch")
            self.send_response(code)
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            try:
                self.wfile.write(reply)
            except OSError:
                pass

        do_POST = do_GET

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    # Failed assertions cannot leave a permanent listener. Normal tests always
    # explicitly stop it; this is an overall failure deadline, never a retry.
    timer = threading.Timer(45, server.shutdown)
    timer.start()
    print(server.server_port, flush=True)
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        timer.cancel()
        server.server_close()


def main():
    if sys.argv[1] == "serve":
        serve(sys.argv[2], int(sys.argv[3]), sys.argv[4], int(sys.argv[5]))
    elif sys.argv[1] == "start":
        process = subprocess.Popen(
            [sys.executable, __file__, "serve", *sys.argv[2:]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print(process.stdout.readline().decode().strip(), flush=True)
        process.stdout.close()
    elif sys.argv[1] == "stop":
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(
            f"http://127.0.0.1:{int(sys.argv[2])}/__stop", timeout=3
        ) as response:
            pid = int(response.read())
            try:
                fd = os.pidfd_open(pid)
            except ProcessLookupError:
                return
        try:
            assert select.select([fd], [], [], 3)[0], "fixture failed to exit"
        finally:
            os.close(fd)


if __name__ == "__main__":
    main()
