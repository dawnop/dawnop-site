"""Synthetic-only backfill recovery, identity, race and output acceptance."""

import argparse
import concurrent.futures
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import contract_fixture
import contract_qiniu_fake
import contract_run

HERE = Path(__file__).resolve().parent
SENTINEL = "K2-RAW-LEAK-SENTINEL"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--sqlite-library", type=Path)
    parser.add_argument(
        "--tool", type=Path, default=HERE / "representation_backfill.py"
    )
    args = parser.parse_args()
    binary = args.binary.resolve()
    work = Path(tempfile.mkdtemp(prefix="site-k2-backfill-"))
    fake = contract_qiniu_fake.serve(0)
    port = fake.server_port
    fake.RequestHandlerClass.bucket.public_base = f"https://localhost:{port}"
    fake_base = f"http://127.0.0.1:{port}"
    from contract_qiniu import Fake

    control = Fake(fake_base)
    passed = []
    try:
        db, live, envf = work / "replica.db", work / "excluded.db", work / "fixture.env"
        contract_fixture.build(str(db))
        contract_fixture.build(str(live))
        contract_run.env_file(envf, db, 0, fake_base)
        env = {"PATH": os.environ["PATH"], "DAWNOP_ENV": str(envf), "TZ": "UTC"}
        migrated = subprocess.run(
            [str(binary), "--migrate"], env=env, capture_output=True, timeout=30
        )
        assert migrated.returncode == 0
        cert, _, _ = contract_qiniu_fake.tls_material()
        config, marker = work / "config.json", work / "identity.json"
        config.write_text(
            json.dumps(
                {
                    "ak": contract_qiniu_fake.FAKE_AK,
                    "sk": contract_qiniu_fake.FAKE_SK,
                    "bucket": contract_qiniu_fake.FAKE_BUCKET,
                    "domain": f"https://localhost:{port}",
                    "rs_host": fake_base,
                    "ca_bundle": str(cert),
                }
            )
        )
        config.chmod(0o600)
        runtime = work / "runtime"
        command = [
            sys.executable,
            str(args.tool.resolve()),
            "--db",
            str(db),
            "--live-db",
            str(live),
            "--identity",
            str(marker),
            "--work",
            str(runtime),
            "--binary",
            str(binary),
            "--config",
            str(config),
            "--interval-ms",
            "0",
            "--max-seconds",
            "60",
            "--page-size",
            "2",
        ]
        if args.sqlite_library:
            command += ["--sqlite-library", str(args.sqlite_library.resolve())]

        def run(*extra, code=0):
            response = subprocess.run(
                command + list(extra), capture_output=True, timeout=80
            )
            assert SENTINEL.encode() not in response.stdout + response.stderr
            assert not response.stderr and response.returncode == code, (
                response.stdout.decode()
            )
            return json.loads(response.stdout)

        def state():
            with sqlite3.connect(db) as conn:
                return list(conn.iterdump())

        def business():
            with sqlite3.connect(db) as conn:
                return conn.execute(
                    "select id,path,is_dir,key,content_type,size,created_at,updated_at from files order by id"
                ).fetchall()

        run("--init-identity")
        initial, logical = state(), business()
        dry = run()
        assert (
            dry["updated"] == 0 and dry["planned"] == dry["files"] and not dry["ready"]
        )
        assert state() == initial
        passed.append("default_dry_run_zero_database_writes")
        assert not run("--phase", "verify", code=4)["ready"]
        incomplete = run(
            "--phase",
            "verify",
            "--write-receipt",
            str(work / "incomplete.json"),
            code=3,
        )
        assert incomplete["failure_category"] == "replica_incomplete"
        applied = run("--phase", "apply")
        assert applied["ready"] and applied["updated"] == applied["files"]
        assert business() == logical
        second = run("--phase", "apply")
        assert second["updated"] == second["requests"] == 0
        assert run("--phase", "resume")["ready"]
        receipt = work / "receipt.json"
        assert run("--phase", "verify", "--write-receipt", str(receipt))["ready"]
        passed.append("apply_verify_idempotence_replica_receipt")
        alias = work / "hardlink.db"
        os.link(live, alias)
        before_calls = len(control.calls())
        result = run("--db", str(alias), code=3)
        assert result["failure_category"] == "live_alias"
        assert (
            run(
                "--db",
                str(alias),
                "--identity",
                str(work / "alias-identity.json"),
                "--init-identity",
                code=3,
            )["failure_category"]
            == "live_alias"
        )
        assert len(control.calls()) == before_calls
        result = run("--role", "live", code=3)
        assert result["failure_category"] == "replica_required"
        config.chmod(0o644)
        assert run(code=3)["failure_category"] == "permissions"
        config.chmod(0o600)
        passed.append("aliases_permissions_and_missing_receipt_refused_before_io")
        # A low id reappears behind the persisted high-water mark.
        with sqlite3.connect(db) as conn:
            ident = conn.execute("select min(id) from files where is_dir=0").fetchone()[
                0
            ]
            conn.execute("delete from files where id=?", (ident,))
            conn.execute(
                "insert into files (id,path,is_dir,key,content_type,size,created_at,updated_at) values (?, ?,0,?,'text/plain',3,'created','updated')",
                (ident, SENTINEL, SENTINEL),
            )
        control.plant(SENTINEL, "abc", "text/plain")
        resumed = run("--phase", "resume")
        assert resumed["ready"] and resumed["updated"] == 1
        passed.append("resume_rescans_new_low_id")
        # Two missing rows force an actual budget checkpoint before completion.
        with sqlite3.connect(db) as conn:
            conn.execute("update files set representation_etag=null where is_dir=0")
        rate_limited = run(
            "--phase", "apply", "--max-seconds", "21", "--interval-ms", "60000", code=6
        )
        assert (
            rate_limited["budget_exhausted"]
            and rate_limited["requests"] == rate_limited["updated"] == 0
        )
        interrupted = run("--phase", "apply", "--max-requests", "2", code=6)
        assert interrupted["budget_exhausted"] and interrupted["updated"] == 1
        assert run("--phase", "resume")["ready"]
        passed.append("budget_interruption_and_resume")
        # Terminating the launcher during HEAD kills the private child group,
        # leaves no partial publication and permits the same checkpoint resume.
        with sqlite3.connect(db) as conn:
            conn.execute(
                "update files set representation_etag=null where path=?", (SENTINEL,)
            )
        control.pause("head")
        terminated = subprocess.Popen(
            command + ["--phase", "apply"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not control.pauses().get("head", {}).get(
            "entered", 0
        ):
            time.sleep(0.02)
        assert control.pauses().get("head", {}).get("entered", 0)
        terminated.terminate()
        stdout, stderr = terminated.communicate(timeout=10)
        assert (
            terminated.returncode == 7
            and not stderr
            and SENTINEL.encode() not in stdout
        )
        assert not json.loads(stdout)["ready"]
        control.release("head")
        assert not list(runtime.glob("request-*"))
        assert run("--phase", "resume")["ready"]
        passed.append("process_termination_cleanup_and_resume")
        # Hold an actual network HEAD while a second SQLite writer replaces the
        # snapshot's key. It must acquire the writer without waiting for HEAD.
        with sqlite3.connect(db) as conn:
            conn.execute(
                "update files set representation_etag=null where path=?", (SENTINEL,)
            )
        control.pause("head")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run, "--phase", "apply", code=5)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not control.pauses().get(
                "head", {}
            ).get("entered", 0):
                time.sleep(0.02)
            assert control.pauses().get("head", {}).get("entered", 0)
            started = time.monotonic()
            with sqlite3.connect(db, timeout=0.5) as conn:
                conn.execute(
                    "update files set key=?,updated_at='concurrent' where path=?",
                    (SENTINEL + "-new", SENTINEL),
                )
            assert time.monotonic() - started < 0.5
            control.plant(SENTINEL + "-new", "xyz", "text/plain")
            control.release("head")
            raced = future.result()
        assert raced["conflicts"] == 1
        with sqlite3.connect(db) as conn:
            key, represented = conn.execute(
                "select key,representation_key from files where path=?", (SENTINEL,)
            ).fetchone()
        assert key == represented == SENTINEL + "-new"
        assert run("--phase", "resume")["ready"]
        passed.append("network_outside_transaction_and_cas_race")
        # All child diagnostic channels are captured and replaced with a fixed
        # category even when a faulty executable prints raw material.
        noisy = work / "noisy"
        noisy.write_text(
            "#!/bin/sh\nprintf '%s\\n' '"
            + SENTINEL
            + "'\nprintf '%s\\n' '"
            + SENTINEL
            + "' >&2\n"
        )
        noisy.chmod(0o700)
        result = run("--binary", str(noisy), code=3)
        assert result["failure_category"] == "child_failure"
        passed.append("all_child_channels_are_sanitized")
        result = {
            "implementation": "LOCAL_PASS",
            "cases": len(passed),
            "passed": passed,
            "gate_outcomes": {
                "synthetic_replica": "LOCAL_PASS",
                "server_replica": "PRODUCTION_PENDING",
                "real_origin": "PRODUCTION_PENDING",
                "clients": "PRODUCTION_PENDING",
                "s13": "PRODUCTION_PENDING",
            },
        }
        result["production_gate"] = (
            "BLOCKED"
            if any(
                value not in {"PASS", "LOCAL_PASS"}
                for value in result["gate_outcomes"].values()
            )
            else "PASS"
        )
        if args.summary:
            args.summary.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        fake.shutdown()
        fake.server_close()
        shutil.rmtree(work)


if __name__ == "__main__":
    sys.exit(main())
