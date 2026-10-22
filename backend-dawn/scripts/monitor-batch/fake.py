"""Independent delayed loopback peer with request and completion records."""

import argparse
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    args = parser.parse_args()
    lock = threading.Lock()
    domains_done = set()

    def event(kind, path):
        with lock, args.events.open("a") as out:
            out.write(
                json.dumps({"kind": kind, "path": path, "time": time.monotonic()})
                + "\n"
            )

    class Peer(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_POST(self):
            self.do_GET()

        def do_GET(self):
            event("start", self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            status, reply = 200, ""
            if self.path == "/close":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if self.path == "/denied":
                status, reply = 503, "denied"
            elif self.path.startswith("/item/"):
                _, _, reply, delay = self.path.split("/")
                time.sleep(int(delay) / 1000)
            elif self.path == "/config":
                time.sleep(0.08)
                reply = '{"version":"fixture-version"}'
            elif self.path == "/utf8":
                reply = b"a\0\xffz"
            elif self.path.startswith("/monitor/"):
                _, _, mode, item = self.path.split("/")
                if item.startswith("tc."):
                    assert self.command == "POST"
                    assert self.headers["Authorization"].startswith("TC3-HMAC-SHA256 ")
                    assert (
                        self.headers["X-TC-Action"]
                        == {
                            "tc.traffic": "DescribeInstancesTrafficPackages",
                            "tc.instance": "DescribeInstances",
                            "tc.trend": "GetMonitorData",
                        }[item]
                    )
                    assert json.loads(body)
                elif item.startswith("qn."):
                    assert self.headers["Authorization"].startswith("Qiniu ")
                if item in ("qn.flux", "qn.bandwidth"):
                    assert mode in domains_done, (
                        "round two started before domain completion"
                    )
                    assert self.command == "POST"
                    assert json.loads(body)["domains"] == "cdn.example"
                # Unequal delays make both rounds finish in a different order.
                time.sleep({"vault.alive": 0.12, "qn.domains": 0.08}.get(item, 0.02))
                if mode == "timeout" and item == "tc.traffic":
                    time.sleep(1.8)
                replies = {
                    "vault.alive": '"alive"',
                    "vault.config": '{"version":"fixture-version"}',
                    "tc.traffic": '{"Response":{"InstanceTrafficPackageSet":[{"TrafficPackageSet":[{"TrafficUsed":7,"TrafficPackageTotal":10,"TrafficPackageRemaining":3,"TrafficOverflow":0}]}]}}',
                    "tc.instance": '{"Response":{"InstanceSet":[{"InstanceName":"box","CPU":4,"Memory":4,"SystemDisk":{"DiskSize":60}}]}}',
                    "tc.trend": '{"Response":{"DataPoints":[{"Timestamps":[1000],"Values":[1.5]}]}}',
                    "qn.space": '{"times":[1000,2000],"datas":[10,20]}',
                    "qn.count": '{"times":[1000,2000],"datas":[10,20]}',
                    "qn.flow": '[{"time":"2026-08-01","values":{"flow":5}}]',
                    "qn.hits": '[{"time":"2026-08-01","values":{"hits":7}}]',
                    "qn.domains": '{"domains":[]}'
                    if mode == "empty"
                    else '{"domains":[{"name":"cdn.example"}]}',
                    "qn.flux": '{"time":["2026-08-01 00:00:00"],"data":{"cdn.example":{"cn":[30]}}}',
                    "qn.bandwidth": '{"time":["2026-08-01 00:00:00"],"data":{"cdn.example":{"cn":[40]}}}',
                    "qn.list": '{"data":[{"status":2,"respack_name":"流量包","used_amount":1,"total_amount":4,"effective_end":"2026-12-01"}]}',
                    "qn.overview": '{"data":[{"item_name":"存储包","total_surplus":3}]}',
                }
                reply = replies[item]
                if (
                    (mode == "failure" and item in ("tc.traffic", "qn.count"))
                    or (mode == "domains-fail" and item == "qn.domains")
                    or (mode == "tune-fail" and item == "qn.flux")
                    or (mode == "billing-fail" and item == "qn.list")
                ):
                    status = 503
                if item == "qn.domains":
                    domains_done.add(mode)
            else:
                status, reply = 404, "missing"
            raw = reply if isinstance(reply, bytes) else reply.encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Item", self.path)
            self.end_headers()
            try:
                self.wfile.write(raw)
                self.wfile.flush()
                event("finish", self.path)
            except (BrokenPipeError, ConnectionResetError):
                event("disconnected", self.path)

    ThreadingHTTPServer.request_queue_size = 64
    server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    print(server.server_port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
