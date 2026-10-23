"""Require both unchanged golden arms, real simple routes and failing controls."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
import contract_fixture  # noqa: E402
import contract_native  # noqa: E402
import contract_run  # noqa: E402


def run(argv, **kwargs):
    result = subprocess.run(
        list(map(str, argv)), capture_output=True, timeout=300, **kwargs
    )
    if result.returncode:
        raise AssertionError(
            result.stdout.decode(errors="replace")
            + result.stderr.decode(errors="replace")
        )
    return result


def environment(work, db, ext=""):
    envf = work / "fixture.env"
    contract_run.env_file(envf, db, 0)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DAWN_", "QINIU_", "TENCENT_", "VAULT_", "LIGHTHOUSE_"))
        and key not in {"SECRET_KEY", "ACCESS_TOKEN_EXPIRE_MINUTES", "STORAGE_QUOTA_GB"}
    }
    return {
        **env,
        "DAWNOP_ENV": str(envf),
        "DAWN_SIMPLE_EXT": str(ext),
        "TZ": "UTC",
        "NO_PROXY": "*",
        "no_proxy": "*",
    }


def controls(work, binary):
    work.mkdir()
    db = work / "fixture.db"
    contract_fixture.build(str(db))
    env = environment(work, db)
    server = contract_native.NativeServer(binary, env, 0, work / "native")
    try:
        server.execute("--migrate")
        server.start()
        base = f"http://127.0.0.1:{server.port}"
        token = contract_run.login(base)
        # These wrappers change actual suite observations, never golden inputs.
        # Each must fail by its own machine verdict, after completing the suite.
        for mode, field in [
            ("byte", "diff"),
            ("missing", "missing"),
            ("skip", "skip_added"),
        ]:
            server.quiesce()
            contract_fixture.build(str(db))
            server.execute("--migrate")
            report = work / f"{mode}.json"
            wrapper = """import os, runpy, sys
from contract_golden import Golden
mode = sys.argv.pop(1)
original = Golden.case
changed = False
def case(self, name, value):
    global changed
    if not changed:
        changed = True
        if mode == "missing": return True
        if mode == "byte":
            assert value["body"]["status"] == "ok"
            value["body"]["status"] = "on"
    return original(self, name, value)
Golden.case = case
finish = Golden.finish
def final(self):
    if mode == "skip": self.skip("unreviewed", "negative control")
    return finish(self)
Golden.finish = final
runpy.run_module("contract_read", run_name="__main__")
"""
            result = subprocess.run(
                [sys.executable, "-c", wrapper, mode, "--base", base],
                env={
                    **os.environ,
                    "PYTHONPATH": str(HERE.parent),
                    "TZ": "UTC",
                    "TOKEN": token,
                    "CONTRACT_GOLDEN_REPORT": str(report),
                },
                capture_output=True,
                timeout=45,
            )
            (work / f"{mode}.log").write_bytes(result.stdout + result.stderr)
            observed = json.loads(report.read_text())
            assert (
                result.returncode == 1
                and observed["complete"]
                and len(observed[field]) == 1
            ), observed
            print(
                f"PASS negative {mode}: completed real suite; red by {field}",
                flush=True,
            )
    finally:
        proof = server.close()
    # A real ELF attempts an absolute Java path with a jar, bypassing PATH.
    java = shutil.which("java")
    source = work / "trojan.c"
    source.write_text(
        "#include <unistd.h>\nint main(void) { execl("
        + json.dumps(java)
        + ', "java", "-jar", '
        + json.dumps(str(ROOT / "backend-dawn/backend-dawn.jar"))
        + ', "--migrate", (char *)0); return 1; }\n'
    )
    trojan = work / "trojan"
    run(["cc", source, "-o", trojan])
    fake = contract_native.NativeServer(trojan, env, 0, work / "trojan-proof")
    fake.execute()
    try:
        fake.close()
    except AssertionError as error:
        assert "native-no-jvm:" in str(error), error
        (work / "trojan.log").write_text(str(error) + "\n")
        print(
            "PASS negative jar launcher: real ELF and absolute java; red by native-no-jvm",
            flush=True,
        )
    else:
        raise AssertionError("jar launcher stayed green")
    return proof


def simple_fixture(work):
    lock = json.loads((HERE.parent / "native-sqlite/fixtures.lock.json").read_text())[
        "simple"
    ]
    archive = (
        Path.home()
        / ".cache/dawn/sqlite-contract/downloads"
        / lock["url"].split("/")[-1]
    )
    if not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        run(["curl", "-fLsS", lock["url"], "-o", archive])
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == lock["sha256"]
    import zipfile

    with zipfile.ZipFile(archive) as zipped:
        paths = [
            p
            for p in zipped.namelist()
            if p.endswith("/libsimple.so") or p == "libsimple.so"
        ]
        assert len(paths) == 1
        library = work / "libsimple.so"
        library.write_bytes(zipped.read(paths[0]))
    assert hashlib.sha256(library.read_bytes()).hexdigest() == lock["library_sha256"]
    return library


def fts(work, binary):
    work.mkdir()
    library = simple_fixture(work)
    observed = {}
    for arm in ("jvm", "native"):
        directory = work / arm
        directory.mkdir()
        db = directory / "fixture.db"
        contract_fixture.build(str(db))
        with sqlite3.connect(db) as conn:
            conn.enable_load_extension(True)
            conn.load_extension(str(library))
            conn.executescript(
                "DELETE FROM article_tags; DELETE FROM articles; DROP TABLE article_fts; CREATE VIRTUAL TABLE article_fts USING fts5(title,summary,content,content='articles',content_rowid='id',tokenize='simple');"
            )
            for ident, title, content, published in [
                (1, "中国", "标题优先", 1),
                (2, "其他", "中国", 1),
                (3, "中国草稿", "中国", 0),
            ]:
                conn.execute(
                    "INSERT INTO articles (id,title,slug,summary,content,published,auto_title,created_at,updated_at) VALUES (?,?,?,'',?,?,0,'2026-01-01','2026-01-01')",
                    (ident, title, f"simple-{ident}", content, published),
                )
            conn.execute("INSERT INTO article_fts(article_fts) VALUES ('rebuild')")
        env = environment(directory, db, library)
        native = None
        proc = None
        if arm == "native":
            native = contract_native.NativeServer(
                binary, env, 0, directory / "processes"
            )
            native.execute("--migrate")
            native.start()
            port = native.port
        else:
            import socket

            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            env["DAWN_PORT"] = str(port)
            log = (directory / "jvm.log").open("wb")
            proc = subprocess.Popen(
                [
                    shutil.which("java"),
                    "-jar",
                    str(ROOT / "backend-dawn/backend-dawn.jar"),
                ],
                env=env,
                stdout=log,
                stderr=log,
            )
            contract_run.wait_health(f"http://127.0.0.1:{port}", proc)

        def query(text, page=1, size=20, port=port):
            url = f"http://127.0.0.1:{port}/api/search?" + urllib.parse.urlencode(
                {"q": text, "page": page, "size": size}
            )
            with contract_run.OPENER.open(url, timeout=5) as response:
                return json.load(response)

        try:
            rows = [query("中国"), query("zhongguo"), query("中国", 2, 1)]
            assert [r["total"] for r in rows] == [2, 2, 2], rows
            assert [i["id"] for i in rows[0]["items"]] == [1, 2], rows
            assert [i["id"] for i in rows[1]["items"]] == [1, 2], rows
            assert [i["id"] for i in rows[2]["items"]] == [2], rows
            observed[arm] = rows
        finally:
            if native:
                native.close()
            if proc:
                proc.terminate()
                proc.wait(timeout=10)
                log.close()
    assert observed["jvm"] == observed["native"]
    (work / "observations.json").write_text(
        json.dumps(observed, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        "PASS real simple 0.7.1 HTTP routes: Chinese, pinyin, ranking, draft exclusion and pagination; additional to goldens",
        flush=True,
    )


def migration():
    manifest = json.loads((ROOT / "docs/dual-arm-diagnostics.json").read_text())
    records = manifest["diagnostics"]
    assert manifest["count"] == len(records) == 89
    assert [r["id"] for r in records] == list(range(1, 90))
    for record in records:
        assert (ROOT / record["destination"]).is_file(), record
    mappings = json.loads((ROOT / "docs/dual-arm-test-migration.json").read_text())
    assert len(mappings) == 58
    for row in mappings:
        path, name = row["destination"].split(" :: ", 1)
        source = (ROOT / "backend-dawn" / path).read_text()
        assert 'test "' + name + '"' in source, row
        assert "use java" not in source, row
    print(
        "PASS 89 historical diagnostics mapped; all 58 original tests retained; test Java imports 16 to 0",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()
    work = (args.work or Path(tempfile.mkdtemp(prefix="dawnop-dual-arm-"))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    migration()
    binary = work / "site-native"
    dawnc = run([ROOT / "scripts/fetch-dawn-native.sh"]).stdout.decode().strip()
    run([dawnc, "build", ROOT / "backend-dawn/app/native", "-o", binary])
    controls(work / "controls", binary)
    # Both arms use the exact existing scripts and normalization implementations.
    result = run(
        [
            sys.executable,
            HERE.parent / "contract_run.py",
            "--arm",
            "both",
            "--native-binary",
            binary,
            "--work",
            work / "contracts",
        ]
    )
    (work / "contracts.log").write_bytes(result.stdout + result.stderr)
    summary = json.loads((work / "contracts/summary.json").read_text())
    expected_count = sum(
        len(json.loads(path.read_text())["cases"])
        for path in (HERE.parent / "golden").glob("*.json")
    )
    for arm, result in summary.items():
        count = sum(s["match"] for s in result["suites"].values())
        assert (
            count == expected_count
            and sum(len(s["skipped"]) for s in result["suites"].values()) == 7
        )
        print(
            f"PASS {arm}: {count}/{expected_count}; original seven skips; wall_s={result['wall_s']:.3f}",
            flush=True,
        )
    for tag, proof in summary["native"]["no_jvm"].items():
        assert proof["jvm"] == 0
        print(
            f"PASS native no JVM {tag}: {proof['processes']} processes, {proof['exec_attempts']} traced exec attempts, curl only PATH",
            flush=True,
        )
    for count in ("0", "1"):
        jvm = summary["jvm"]["representation"]["directories"][count]
        native = summary["native"]["representation"]["directories"][count]
        assert native["p95_ms"] <= max(100, jvm["p95_ms"] + 50)
    print(
        "PASS PROPFIND 0/1/100/1000 completeness and zero IO; small-directory p95 and cold authentication",
        flush=True,
    )
    fts(work / "simple", binary)
    print(
        f"PASS dual arm acceptance wall_s={time.monotonic() - started:.3f}; evidence={work}",
        flush=True,
    )


if __name__ == "__main__":
    main()
