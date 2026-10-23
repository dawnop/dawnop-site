"""Compile real JVM initializer regressions and reject stale packaged drivers."""

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
from check_jdbc_artifact import check  # noqa: E402
from mutant_workdir import require_workdir  # noqa: E402

MAIN = """use db/db.{with_db, config, probe_engine}
use db/jdbc.{open_ext, with_connection}
use site_core/db/runtime
use site_core/db/sql.{scalar_int, execute}
use std/str
fn check(ok: Bool, label: String) -> Unit = if not ok { panic("assertion: ${label}") }
fn required[T](r: Result[T, String]) -> T = match r { Ok(v) -> v
  Err(m) -> panic(m) }
fn refusal(path: String, ext: String, label: String, phrase: String) -> Unit !io = {
  let r = catch_panic(() => with_db(path, ext, () => ()))
  check(match r { Err(e) -> str.contains(e.message, phrase)
    Ok(_) -> false }, label)
  let r2 = catch_panic(() => runtime.with_db(config(path, ext), () => ()))
  check(match r2 { Err(e) -> str.contains(e.message, phrase)
    Ok(_) -> false }, label)
}
pub fn main() -> Unit !io = {
  let a = args()
  if a[0] == "version" { println(probe_engine()) }
  else if a[0] == "wal" { refusal(":memory:", "", "startup-wal", "SQLite did not enter WAL mode") }
  else if a[0] == "wal-sql" {
    with_connection(open_ext("jdbc:sqlite:${a[1]}"), () => {
      required(execute("create table wal_lock(id integer)", []))
      required(execute("begin immediate", []))
      refusal(a[1], "", "startup-wal-sql", "database is locked")
      required(execute("rollback", []))
    })
  }
  else if a[0] == "extension" { refusal(a[1], a[1] ++ ".missing", "startup-extension", "missing") }
  else { with_db(a[1], "", () => check(required(scalar_int("PRAGMA busy_timeout", [])) == 5000, "timeout")) }
}
"""


def run(argv, good=True):
    r = subprocess.run(
        list(map(str, argv)), text=True, capture_output=True, timeout=180
    )
    if good and r.returncode:
        raise AssertionError(f"{argv}\n{r.stdout}{r.stderr}")
    return r


def project(work, name, change=None, old=False):
    root = work / name
    root.mkdir()
    (root / ".mutant-workdir").write_text(name)
    require_workdir(root, root=BACKEND.parent)
    app = root / "app"
    shutil.copytree(BACKEND / "app/jvm/src/db", app / "src/db")
    (app / "src/main.dawn").write_text(MAIN)
    manifest = (
        (BACKEND / "app/jvm/dawn.toml")
        .read_text()
        .replace('site_core = "../../core"', f'site_core = "{BACKEND / "core"}"')
    )
    if old:
        manifest = manifest.replace("3.53.4.0", "3.36.0.3")
    else:
        shutil.copy2(BACKEND / "app/jvm/dawn.lock", app / "dawn.lock")
    (app / "dawn.toml").write_text(manifest)
    if change:
        source = app / "src/db/db.dawn"
        text = source.read_text()
        before, after = change
        assert text.count(before) == 1, (name, text.count(before))
        if "QueryResult {" in after:
            text = text.replace(
                "Db, ColStr, VStr, PStr, query}",
                "Db, ColStr, VStr, PStr, query, QueryResult}",
            )
        source.write_text(text.replace(before, after))
    return app


def acceptance(work, dawn, jar):
    lock = BACKEND / "app/jvm/dawn.lock"
    # A real obsolete jar is added next to a valid artifact, never to the checkout.
    package = work / "stale"
    package.mkdir()
    shutil.copy2(jar, package / "app.jar")
    shutil.copytree(jar.parent / "lib", package / "lib")
    old = project(work, "old-driver", old=True)
    oldjar = work / "old.jar"
    run([dawn, "build", old, "-o", oldjar])
    r = run(["java", "-jar", oldjar, "version"], good=False)
    (work / "old-driver.log").write_text(r.stdout + r.stderr)
    assert (
        r.returncode != 0
        and "SQLite 3.36.0 refused: requires >= 3.51.3" in r.stdout + r.stderr
    )
    print(
        "PASS negative old driver: actual SQLite 3.36.0 refused by version gate",
        flush=True,
    )
    shutil.copy2(work / "lib/sqlite-jdbc-3.36.0.3.jar", package / "lib")
    try:
        check(package / "app.jar", lock)
    except ValueError as e:
        assert "runtime lib closure mismatch" in str(e), e
        (work / "stale.log").write_text(str(e))
    else:
        raise AssertionError("stale driver was accepted")
    print(
        "PASS negative stale lib: actual old jar rejected by runtime lib closure mismatch",
        flush=True,
    )
    for name, before, after, mode, reason in [
        (
            "swallow-wal",
            'if mode.rows != [[VStr("wal")]] { panic("SQLite did not enter WAL mode") }',
            "let _ = mode",
            "wal",
            "startup-wal",
        ),
        (
            "swallow-extension",
            'let _ = required((c.query)("select load_extension(?)", [PStr(ext)], [ColStr]))',
            'let _ = (c.query)("select load_extension(?)", [PStr(ext)], [ColStr])',
            "extension",
            "startup-extension",
        ),
        (
            "swallow-wal-sql-error",
            'let mode = required((c.query)("PRAGMA journal_mode=WAL", [], [ColStr]))',
            'let mode = match (c.query)("PRAGMA journal_mode=WAL", [], [ColStr]) { Ok(v) -> v\n Err(_) -> QueryResult { names = ["journal_mode"], rows = [[VStr("wal")]] } }',
            "wal-sql",
            "startup-wal-sql",
        ),
    ]:
        app = project(work, name, (before, after))
        target = work / name / "probe.jar"
        run([dawn, "build", app, "-o", target])
        r = run(["java", "-jar", target, mode, work / (name + ".db")], good=False)
        (work / (name + ".log")).write_text(r.stdout + r.stderr)
        assert r.returncode != 0 and "assertion: " + reason in r.stdout + r.stderr, (
            name,
            r,
        )
        print(
            f"PASS negative {name}: compiled then red by assertion: {reason}",
            flush=True,
        )
    # The exact same consumer is green with the unmodified initializer.
    app = project(work, "positive")
    target = work / "positive/probe.jar"
    run([dawn, "build", app, "-o", target])
    for mode in ("version", "wal", "wal-sql", "extension", "ok"):
        r = run(["java", "-jar", target, mode, work / "positive.db"])
        (work / (mode + ".log")).write_text(r.stdout + r.stderr)
        if mode == "version":
            assert "sqlite_version=3.53.4 sqlite_source_id=" in r.stdout
    check(jar, lock)
    r = run(["java", "-jar", jar, "--engine-info"])
    assert "sqlite_version=3.53.4 sqlite_source_id=" in r.stderr and not r.stdout
    print(r.stderr.strip(), flush=True)
    print(
        "PASS strict JVM initialization both entry points and packaged memory probe",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dawn", type=Path, required=True)
    parser.add_argument("--jar", type=Path, required=True)
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="site-jdbc-init-") as temporary:
        work = args.work or Path(temporary)
        work.mkdir(parents=True, exist_ok=True)
        assert not any(work.iterdir()), "work directory must be empty"
        acceptance(work, args.dawn.resolve(), args.jar.resolve())
    print(
        f"PASS JDBC initialization acceptance wall_s={time.monotonic() - started:.3f}"
    )


if __name__ == "__main__":
    main()
