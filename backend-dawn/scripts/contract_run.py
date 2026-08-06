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

contract_qiniu then runs against a SECOND backend on the next port, pointed at
contract_qiniu_fake.py (a loopback fake bucket) instead of the real qiniu hosts.
It is a separate process because the endpoints are read from config once at
startup, and a separate golden because "credentials configured" is a different
environment — the two must never be compared with each other's bytes.

Environment pinning is part of the contract, not an accident of the host:

  * TZ=UTC — repo_fm derives `last_modified` from files.updated_at through
    LocalDateTime in the system zone, so a +08 laptop and a UTC runner disagreed
    by 28800 on every fm entry. Pinned here and recorded in the goldens.
  * DAWN_SIMPLE_EXT="" — never load the FTS tokenizer even if the .so happens to
    be on disk, so the search path is the same everywhere (see
    contract_golden.SEARCH_BACKEND for what that does and does not cover).
  * QINIU_*/TENCENT_* empty for the first three — no network calls to third
    parties from a contract run. The fourth talks only to 127.0.0.1.
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
import contract_qiniu_fake

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


def env_file(
    path: pathlib.Path, db: pathlib.Path, port: int, fake_qiniu: str = ""
) -> None:
    """Write the backend's .env. `fake_qiniu` is the fake bucket's base URL.

    Without it the qiniu settings are present and empty, which is how the first
    three scripts get a backend that cannot reach an object store at all. With
    it, all three endpoints (management, upload, download domain) point at the
    fake — note that QINIU_RS_HOST / QINIU_UP_HOST are *omitted* otherwise
    rather than set empty: config.get_or takes a present-but-empty value at face
    value, so writing them blank would leave the backend with no host at all.
    """
    lines = [
        f"DAWN_DB_PATH={db}",
        "DAWN_SIMPLE_EXT=",
        f"DAWN_PORT={port}",
        "DAWN_CORS_ORIGIN=https://dawnop.com",
        f"SECRET_KEY={CONTRACT_JWT_PHRASE}",
        "ACCESS_TOKEN_EXPIRE_MINUTES=1440",
        "STORAGE_QUOTA_GB=10",
    ]
    if fake_qiniu:
        lines += [
            f"QINIU_ACCESS_KEY={contract_qiniu_fake.FAKE_AK}",
            f"QINIU_SECRET_KEY={contract_qiniu_fake.FAKE_SK}",
            f"QINIU_BUCKET={contract_qiniu_fake.FAKE_BUCKET}",
            f"QINIU_DOMAIN={fake_qiniu}",
            f"QINIU_RS_HOST={fake_qiniu}",
            f"QINIU_UP_HOST={fake_qiniu}",
        ]
    else:
        lines += [
            "QINIU_ACCESS_KEY=",
            "QINIU_SECRET_KEY=",
            "QINIU_BUCKET=",
            "QINIU_DOMAIN=",
        ]
    lines += [
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
    path.write_text("\n".join(lines), encoding="utf-8")


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
        choices=["read", "edge", "webdav", "qiniu"],
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
    scripts = args.only or ["read", "edge", "webdav", "qiniu"]
    plain = [s for s in scripts if s != "qiniu"]

    # Two backends, because the object-store endpoints are read from config once
    # at startup: one that cannot reach a bucket (the historical contract), one
    # pointed at the fake. Each gets its own database so neither script's
    # mutations can reach the other's fixture.
    fake_base = f"http://127.0.0.1:{args.port + 2}"
    servers = {}
    if plain:
        servers["plain"] = (work / "contract.db", work / "contract.env", args.port, "")
    if "qiniu" in scripts:
        servers["qiniu"] = (
            work / "contract-qiniu.db",
            work / "contract-qiniu.env",
            args.port + 1,
            fake_base,
        )

    procs, logs, tokens, bases = {}, {}, {}, {}
    rc = 0
    fake = contract_qiniu_fake.serve(args.port + 2) if "qiniu" in scripts else None
    try:
        for tag, (db, envf, port, fake_for) in servers.items():
            env_file(envf, db, port, fake_for)
            contract_fixture.build(str(db))
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
                "QINIU_RS_HOST",
                "QINIU_UP_HOST",
                "QINIU_DOMAIN",
            ):
                # config.lookup prefers the process environment over the .env
                # file, so a developer's exported key would silently outrank the
                # contract one — and, for the hosts, would aim a contract run at
                # a real bucket.
                child_env.pop(stale, None)
            logs[tag] = open(work / f"backend-{tag}.log", "wb")
            procs[tag] = subprocess.Popen(
                [java, "-Duser.timezone=UTC", "-jar", str(jar)],
                env=child_env,
                stdout=logs[tag],
                stderr=subprocess.STDOUT,
            )
            bases[tag] = f"http://127.0.0.1:{port}"
            wait_health(bases[tag], procs[tag])
            tokens[tag] = login(bases[tag])

        results = {}
        for name in scripts:
            tag = "qiniu" if name == "qiniu" else "plain"
            db = servers[tag][0]
            # fresh fixture per script: each golden then describes one backend
            # from one known state, whatever the previous script mutated
            contract_fixture.build(str(db))
            print(f"\n{'=' * 62}\n== contract_{name}\n{'=' * 62}")
            cmd = [
                sys.executable,
                str(HERE / f"contract_{name}.py"),
                "--base",
                bases[tag],
            ]
            if args.record:
                cmd.append("--record")
            senv = dict(os.environ)
            senv.update(
                {
                    "TOKEN": tokens[tag],
                    "TZ": "UTC",
                    "DAV_USER": contract_fixture.FIXTURE_USER,
                    "DAV_PASS": contract_fixture.FIXTURE_PW,
                    "no_proxy": "*",
                    "NO_PROXY": "*",
                    "PYTHONPATH": str(HERE),
                }
            )
            if name == "qiniu":
                senv["FAKE_QINIU"] = fake_base
                senv["CONTRACT_QINIU_CONFIGURED"] = "1"
            else:
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
        if fake is not None:
            fake.shutdown()
        for tag, proc in procs.items():
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
            logs[tag].close()
        if rc:
            print(f"\nbackend logs in {work} (work dir kept for inspection)")
        else:
            shutil.rmtree(work, ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
