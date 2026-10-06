#!/usr/bin/env python3
"""Exercise public Markdown, durable limits and management against an isolated JVM."""

import argparse
import concurrent.futures
import json
import os
import pathlib
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import contract_fixture
import contract_run

ROOT = pathlib.Path(__file__).resolve().parents[2]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    opts = parser.parse_args()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", opts.port))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="paste-contract-") as directory:
        work = pathlib.Path(directory)
        db = work / "test.db"
        contract_fixture.build(str(db))
        contract_run.env_file(work / "test.env", db, port)
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(
                ("DAWN_", "QINIU_", "TENCENT_", "VAULT_", "SECRET_KEY", "ACCESS_TOKEN_")
            )
        }
        env.update(
            DAWNOP_ENV=str(work / "test.env"), TZ="UTC", NO_PROXY="*", no_proxy="*"
        )
        java = shutil.which("java")
        command = [java, "-jar", str(ROOT / "backend-dawn/backend-dawn.jar")]
        log = (work / "server.log").open("wb")
        proc = None
        base = f"http://127.0.0.1:{port}"

        def start():
            p = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
            contract_run.wait_health(base, p)
            return p

        def request(
            path="",
            status=200,
            method="GET",
            body=None,
            source=None,
            admin=False,
            data=None,
        ):
            headers = {}
            if body is not None:
                data = json.dumps(body, ensure_ascii=True).encode()
            if data is not None:
                headers["Content-Type"] = "application/json"
            if source is not None:
                headers["X-Real-IP"] = source
            if admin:
                headers["Authorization"] = f"Bearer {token}"
            req = urllib.request.Request(
                base + "/api/pastes" + urllib.parse.quote(path, safe="/?=&%"),
                data=data,
                headers=headers,
                method=method,
            )
            try:
                with OPENER.open(req, timeout=15) as r:
                    code, raw, hs = r.status, r.read(), r.headers
            except urllib.error.HTTPError as r:
                code, raw, hs = r.code, r.read(), r.headers
            assert code == status, (path, method, code, status, raw[:200])
            parsed = raw.decode() if path.endswith("/raw") else json.loads(raw)
            if code in (429, 503):
                assert int(hs["Retry-After"]) > 0
            return parsed

        def sql(statement, args=()):
            with sqlite3.connect(db, timeout=10) as c:
                return c.execute(statement, args).fetchall()

        def setting(**changes):
            values = request("/admin/settings", admin=True)
            values.update(changes)
            request("/admin/settings", method="PUT", body=values, admin=True)

        def post(text="正文", status=201, source="192.0.2.1", **extra):
            return request(
                status=status,
                method="POST",
                body={"content": text, **extra},
                source=source,
            )

        try:
            proc = start()
            token = contract_run.login(base)
            request("/config")
            for path, method, body in [
                ("/admin", "GET", None),
                ("/admin/settings", "GET", None),
                ("/admin/settings", "PUT", {}),
                ("/admin/stats", "GET", None),
                ("/admin/delete", "POST", {"ids": ["x"]}),
                ("/admin/cleanup", "POST", {}),
                ("/admin/x", "GET", None),
                ("/admin/x/retain", "POST", {}),
            ]:
                request(path, 401, method, body)
            source_text = "# 中文\n\n```viz\nalert(1)\n```\n<script>x</script>\n"
            p = post(source_text, title="Markdown")
            assert len(p["id"]) == 32 and p["content"] == source_text
            assert p["size_bytes"] == len(source_text.encode())
            assert p["expires_at"] - p["created_at"] == 7 * 86400
            assert request("/" + p["id"]) == p
            assert request("/" + p["id"] + "/raw") == source_text
            listing = request("?q=中文")
            assert request("?q=script")["total"] == 1
            assert request("?q=viz")["total"] == 1
            assert request("?q=%22%20OR%20script")["total"] == 0
            assert listing["total"] == 1 and "content" not in listing["items"][0]
            assert set(p) == {
                "id",
                "title",
                "content",
                "created_at",
                "expires_at",
                "size_bytes",
            }
            for body in (
                {},
                {"content": "  \n"},
                {"content": "a\x00b"},
                {"content": "x", "days": 0},
                {"content": "x", "days": 8},
                {"content": "x", "title": "😀" * 121},
                {"content": False},
                {"content": "x", "days": 1.0},
            ):
                request(status=422, method="POST", body=body)
            post("x", status=400, source="localhost")
            post("x", status=400, source="127.1")
            request(status=400, method="POST", data=b'{"content":"\xff"}')
            setting(max_bytes=6)
            post("你好", source="192.0.2.2")
            post("你好a", 413, "192.0.2.2")
            setting(max_bytes=262144)
            post("x" * 262144, source="192.0.2.3")
            post("x" * 262145, 413, "192.0.2.3")
            for _ in range(5):
                post(source="192.0.2.10")
            # Keep this boundary check deterministic if the run straddles a UTC minute.
            sql(
                "update paste_budgets set minute=?,minute_count=5 where source!='global' and count=5",
                (int(time.time()) // 60,),
            )
            post(status=429, source="192.0.2.10")
            # Persisted minute window is pinned, not dependent on crossing a wall-clock minute.
            setting(source_minute=60, source_day=2)
            d = post(source="192.0.2.20")
            post(source="192.0.2.20")
            request("/admin/delete", method="POST", body={"ids": [d["id"]]}, admin=True)
            post(status=429, source="192.0.2.20")
            proc.terminate()
            proc.wait(timeout=10)
            proc = start()
            post(status=429, source="192.0.2.20")
            # IPv6 /64 and IPv4 mapped literals share each respective budget.
            post(source="2001:db8:1:2::1")
            post(source="2001:0db8:1:2::ffff")
            post(status=429, source="2001:db8:1:2::2")
            post(source="192.0.2.21")
            post(source="::ffff:192.0.2.21")
            post(status=429, source="192.0.2.21")
            setting(source_day=30, source_day_bytes=6)
            post("你好", source="192.0.2.22")
            post("a", 429, "192.0.2.22")
            setting(source_day_bytes=2097152, global_day=1)
            post(status=503, source="192.0.2.99")
            setting(global_day=1000, global_day_bytes=1)
            post(status=503, source="192.0.2.99")
            setting(global_day_bytes=10485760, stored_count=1)
            post(status=503, source="192.0.2.99")
            setting(stored_count=20000, stored_bytes=1)
            post(status=503, source="192.0.2.99")
            assert request("/" + p["id"])["content"] == source_text
            setting(stored_bytes=104857600, source_day=3)

            def concurrent_post(_):
                try:
                    return post(source="192.0.2.30")["id"]
                except AssertionError as e:
                    assert e.args[0][2] == 429, e
                    return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                accepted = list(pool.map(concurrent_post, range(10)))
            assert sum(v is not None for v in accepted) == 3, accepted
            counters = sql(
                "select source,count,bytes from paste_budgets where source!='global'"
            )
            assert all(len(row[0]) == 64 for row in counters)
            # Public expiry applies before physical cleanup; admin can inspect and retain.
            sql("update pastes set expires_at=0 where id=?", (p["id"],))
            request("/" + p["id"], 404)
            request("/" + p["id"] + "/raw", 404)
            assert request("?q=中文")["total"] == 0
            assert request("/admin/" + p["id"], admin=True)["content"] == source_text
            request("/admin/" + p["id"] + "/retain", method="POST", admin=True)
            assert request("/" + p["id"])["expires_at"] is None
            request("/admin/delete", method="POST", body={"ids": [p["id"]]}, admin=True)
            request("/" + p["id"], 404)
            bad = request("/admin/settings", admin=True)
            bad["global_day"] = False
            request("/admin/settings", 422, "PUT", bad, admin=True)
            assert request("/admin/settings", admin=True)["global_day"] == 1000
            bad = request("/admin/settings", admin=True)
            del bad["max_bytes"]
            bad["unknown"] = 1
            request("/admin/settings", 422, "PUT", bad, admin=True)
            request("/admin/delete", 422, "POST", {"ids": []}, admin=True)
            request("/admin/delete", 422, "POST", {"ids": ["missing", 1]}, admin=True)
            assert request("/admin?status=expired", admin=True)["total"] == 0
            assert request("/admin?status=permanent", admin=True)["total"] == 0
            # The timer CLI has no HTTP dependency and must preserve live records and daily quotas.
            sql(
                "update pastes set expires_at=0 where id=?",
                (accepted[0] or next(v for v in accepted if v),),
            )
            before = len(sql("select * from paste_budgets"))
            result = subprocess.run(
                command + ["--cleanup-pastes"], env=env, capture_output=True, timeout=20
            )
            assert result.returncode == 0, result.stderr.decode()
            assert not sql("select id from pastes where expires_at<=0")
            assert len(sql("select * from paste_budgets")) == before
            post(status=429, source="192.0.2.30")
            print(
                "PASS: public Markdown, auth, UTF-8, byte/title/expiry limits, source/global/capacity quotas, IPv6, concurrency, restart, deletion and timer"
            )
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)
            log.close()


if __name__ == "__main__":
    main()
