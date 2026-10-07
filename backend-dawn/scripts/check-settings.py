#!/usr/bin/env python3
"""Check administrator settings, partial updates and transactional refusal in isolation."""

import concurrent.futures
import json
import os
import pathlib
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import urllib.error
import urllib.request

import contract_fixture
import contract_run

ROOT = pathlib.Path(__file__).resolve().parents[2]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="settings-contract-") as directory:
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
        env.update(DAWNOP_ENV=str(work / "test.env"), TZ="UTC")
        base = f"http://127.0.0.1:{port}"
        command = [
            shutil.which("java"),
            "-jar",
            str(ROOT / "backend-dawn/backend-dawn.jar"),
        ]
        proc = None
        with (work / "server.log").open("wb") as log:

            def start():
                p = subprocess.Popen(
                    command, env=env, stdout=log, stderr=subprocess.STDOUT
                )
                contract_run.wait_health(base, p)
                return p

            def sql(statement):
                with sqlite3.connect(db) as c:
                    return c.execute(statement).fetchall()

            def request(body=None, status=200, admin=True):
                headers = {"Content-Type": "application/json"}
                if admin:
                    headers["Authorization"] = f"Bearer {token}"
                req = urllib.request.Request(
                    base + "/api/settings",
                    headers=headers,
                    data=None if body is None else json.dumps(body).encode(),
                    method="GET" if body is None else "PUT",
                )
                try:
                    with OPENER.open(req, timeout=15) as r:
                        code, raw = r.status, r.read()
                except urllib.error.HTTPError as r:
                    code, raw = r.code, r.read()
                assert code == status, (code, status, raw[:200])
                return json.loads(raw)

            try:
                proc = start()
                token = contract_run.login(base)
                request(admin=False, status=401)
                request({"admin_compact": 1}, admin=False, status=401)
                initial = request()
                assert initial["admin_page_size"] == 20
                assert initial["drop_expiry_hours"] == 24
                assert initial["drop_file_max_mb"] == 100
                # A settings update must never rewrite issued links.
                sql(
                    "insert into upload_drops (token,token_hash,dir,created_at,expires_at,max_files,max_file_bytes,max_total_bytes) values ('fixture-token','fixture-hash','','2026-01-01',2000000000,3,10,20)"
                )
                issued = sql("select * from upload_drops")
                valid = {
                    "admin_page_size": 30,
                    "admin_compact": 1,
                    "drop_expiry_hours": 3,
                    "drop_max_files": 7,
                    "drop_file_max_mb": 50,
                    "drop_total_max_mb": 200,
                }
                updated = request(valid)
                assert all(updated[k] == v for k, v in valid.items())
                assert updated["upload_concurrency"] == initial["upload_concurrency"]
                assert sql("select * from upload_drops") == issued
                for changes in [
                    {"admin_page_size": 9},
                    {"admin_page_size": 51},
                    {"admin_compact": 2},
                    {"drop_expiry_hours": 721},
                    {"drop_max_files": 1001},
                    {"drop_file_max_mb": 5121},
                    {"drop_total_max_mb": 51201},
                    {"admin_compact": True},
                    {"admin_compact": 1.0},
                    {"admin_compact": "1"},
                    {"admin_compact": None},
                    {"upload_concurrency": 7, "unknown": 3},
                    {"upload_concurrency": 7, "drop_file_max_mb": 201},
                    {"upload_concurrency": 7, "drop_total_max_mb": 49},
                ]:
                    request(changes, status=400)
                    assert request() == updated, changes
                # Force a failure after a preceding write, then check rollback.
                sql(
                    "create trigger fail_settings before insert on settings when new.key='drop_max_files' begin select raise(abort,'fixture failure'); end"
                )
                request({"admin_compact": 0, "drop_max_files": 8}, status=400)
                assert request() == updated
                sql("drop trigger fail_settings")

                # Simultaneous partial writes cannot create an invalid pair.
                def racing(changes):
                    try:
                        return request(changes)
                    except AssertionError as e:
                        assert e.args[0][0] == 400
                        return None

                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    list(
                        pool.map(
                            racing,
                            [{"drop_file_max_mb": 150}, {"drop_total_max_mb": 100}],
                        )
                    )
                persisted = request()
                assert persisted["drop_total_max_mb"] >= persisted["drop_file_max_mb"]
                proc.terminate()
                proc.wait(timeout=10)
                proc = start()
                assert request() == persisted
                assert sql("select * from upload_drops") == issued
            finally:
                if proc is not None:
                    proc.terminate()
                    proc.wait(timeout=10)
    print(
        "Settings: defaults, auth, bounds, partial updates, rollback, concurrency and restart PASS"
    )


if __name__ == "__main__":
    main()
