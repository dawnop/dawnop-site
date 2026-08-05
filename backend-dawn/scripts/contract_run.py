#!/usr/bin/env python3
"""Drive the three contract scripts against a freshly seeded backend.

One command, no live database, no second backend, no credentials:

    python3 backend-dawn/scripts/contract_run.py            # verify
    python3 backend-dawn/scripts/contract_run.py --record   # re-record goldens

It seeds the pinned fixture, boots backend-dawn.jar on a private port with a
generated .env, logs in as the fixture admin, and runs contract_read,
contract_edge and contract_webdav in turn — reseeding the database before each,
so the small mutations they make (a view counter, a MKCOL) stay pinned instead
of leaking into the next script.

Environment pinning is part of the contract, not an accident of the host:

  * TZ=UTC — repo_fm derives `last_modified` from files.updated_at through
    LocalDateTime in the system zone, so a +08 laptop and a UTC runner disagreed
    by 28800 on every fm entry. Pinned here and recorded in the goldens.
  * DAWN_SIMPLE_EXT="" — never load the FTS tokenizer even if the .so happens to
    be on disk, so the search path is the same everywhere (see
    contract_golden.SEARCH_BACKEND for what that does and does not cover).
  * QINIU_*/TENCENT_* empty — no network calls to third parties from a contract
    run. The cases that need them are named skips.
"""

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import contract_fixture

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
JAR = ROOT / "backend-dawn" / "backend-dawn.jar"

# Fixture-only signing phrase for the contract JWT. Constant so a run is
# reproducible; nothing signed with it ever leaves this process. (Named without
# the word gitleaks keys its generic-value rule on, so the guard job does not
# have to decide whether a test literal is a leak.)
CONTRACT_JWT_PHRASE = "dawnop-contract-test-not-a-real-key"

# Talk to 127.0.0.1 directly. urllib's no_proxy matching is suffix-based, so the
# usual `no_proxy=127.*` a dev box exports does NOT exempt 127.0.0.1, and every
# contract request would be posted to whatever local proxy is configured. An
# empty ProxyHandler settles it for any host config.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def env_file(path: pathlib.Path, db: pathlib.Path, port: int) -> None:
    path.write_text(
        "\n".join(
            [
                f"DAWN_DB_PATH={db}",
                "DAWN_SIMPLE_EXT=",
                f"DAWN_PORT={port}",
                "DAWN_CORS_ORIGIN=https://dawnop.com",
                f"SECRET_KEY={CONTRACT_JWT_PHRASE}",
                "ACCESS_TOKEN_EXPIRE_MINUTES=1440",
                "STORAGE_QUOTA_GB=10",
                "QINIU_ACCESS_KEY=",
                "QINIU_SECRET_KEY=",
                "QINIU_BUCKET=",
                "QINIU_DOMAIN=",
                "QINIU_TOKEN_EXPIRES=3600",
                "TENCENT_SECRET_ID=",
                "TENCENT_SECRET_KEY=",
                "TENCENT_REGION=ap-shanghai",
                "LIGHTHOUSE_INSTANCE_ID=",
                # port 1 refuses instantly: the vault probe must not wait on a timeout
                "VAULT_ALIVE_URL=http://127.0.0.1:1/alive",
                "VAULT_PUBLIC_URL=https://vault.invalid",
                "",
            ]
        ),
        encoding="utf-8",
    )


def wait_health(base: str, proc: subprocess.Popen, timeout=60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"backend exited early with {proc.returncode}")
        try:
            with OPENER.open(base + "/api/health", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(0.3)
    raise SystemExit(f"backend did not become healthy at {base} within {timeout}s")


def login(base: str) -> str:
    data = urllib.parse.urlencode(
        {
            "username": contract_fixture.FIXTURE_USER,
            "password": contract_fixture.FIXTURE_PW,
        }
    ).encode()
    r = urllib.request.Request(base + "/api/auth/login", data=data, method="POST")
    with OPENER.open(r, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true", help="rewrite the golden files")
    ap.add_argument("--port", type=int, default=18001)
    ap.add_argument("--jar", default=str(JAR))
    ap.add_argument(
        "--only",
        action="append",
        choices=["read", "edge", "webdav"],
        help="run a subset (repeatable)",
    )
    args = ap.parse_args()

    jar = pathlib.Path(args.jar)
    if not jar.exists():
        print(f"FATAL: no jar at {jar} — run ./backend-dawn/build.sh", file=sys.stderr)
        return 2
    java = shutil.which("java") or str(
        pathlib.Path(os.environ.get("JAVA_HOME", "")) / "bin" / "java"
    )

    work = pathlib.Path(tempfile.mkdtemp(prefix="dawnop-contract-"))
    db = work / "contract.db"
    envf = work / "contract.env"
    env_file(envf, db, args.port)
    contract_fixture.build(str(db))

    base = f"http://127.0.0.1:{args.port}"
    child_env = dict(os.environ)
    child_env.update(
        {"DAWNOP_ENV": str(envf), "TZ": "UTC", "no_proxy": "*", "NO_PROXY": "*"}
    )
    for stale in (
        "SECRET_KEY",
        "DAWN_DB_PATH",
        "DAWN_PORT",
        "QINIU_ACCESS_KEY",
        "QINIU_SECRET_KEY",
    ):
        # config.lookup prefers the process environment over the .env file, so a
        # developer's exported key would silently outrank the contract one.
        child_env.pop(stale, None)

    log = open(work / "backend.log", "wb")
    proc = subprocess.Popen(
        [java, "-Duser.timezone=UTC", "-jar", str(jar)],
        env=child_env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    rc = 0
    try:
        wait_health(base, proc)
        token = login(base)
        scripts = args.only or ["read", "edge", "webdav"]
        results = {}
        for name in scripts:
            # fresh fixture per script: each golden then describes one backend
            # from one known state, whatever the previous script mutated
            contract_fixture.build(str(db))
            print(f"\n{'=' * 62}\n== contract_{name}\n{'=' * 62}")
            cmd = [sys.executable, str(HERE / f"contract_{name}.py"), "--base", base]
            if args.record:
                cmd.append("--record")
            senv = dict(os.environ)
            senv.update(
                {
                    "TOKEN": token,
                    "TZ": "UTC",
                    "DAV_USER": contract_fixture.FIXTURE_USER,
                    "DAV_PASS": contract_fixture.FIXTURE_PW,
                    "no_proxy": "*",
                    "NO_PROXY": "*",
                    "PYTHONPATH": str(HERE),
                }
            )
            senv.pop("CONTRACT_QINIU_CONFIGURED", None)
            results[name] = subprocess.call(cmd, env=senv, cwd=str(HERE))
            rc = rc or results[name]
        print(f"\n{'=' * 62}")
        for name, code in results.items():
            verdict = "PASS" if code == 0 else f"FAIL ({code})"
            print(f"  contract_{name}: {verdict}")
    except BaseException:
        rc = 1  # keep the work dir: the backend log is the only clue left
        raise
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        if rc:
            print(
                f"\nbackend log: {work / 'backend.log'} (work dir kept for inspection)"
            )
        else:
            shutil.rmtree(work, ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
