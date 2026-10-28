"""Compare real JDBC/native handlers, require simple FTS, and test cleanup."""

import argparse
import hashlib
import importlib.util
import json
import os
import selectors
import shutil
import sqlite3
import statistics
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
ROOT = BACKEND.parent
HASH = "e89861b527cb60ab91654d19660a7f8686737e6ad644f0e0833c4b958baeb626"
ARCHIVE = Path(os.environ.get("DAWN_PKG_CACHE", Path.home() / ".dawn/pkg")) / (
    "d1-" + HASH
)
LOCK = json.loads((HERE / "fixtures.lock.json").read_text())
GUARD_SPEC = importlib.util.spec_from_file_location(
    "mutant_workdir", BACKEND / "scripts/mutant_workdir.py"
)
GUARD = importlib.util.module_from_spec(GUARD_SPEC)
GUARD_SPEC.loader.exec_module(GUARD)
CACHE_SPEC = importlib.util.spec_from_file_location(
    "sqlite_ci_cache", BACKEND / "scripts/sqlite_ci_cache.py"
)
CACHE = importlib.util.module_from_spec(CACHE_SPEC)
CACHE_SPEC.loader.exec_module(CACHE)
SAN = CACHE.SAN


def run(argv, *, env=None, good=True, timeout=300):
    started = time.monotonic()
    r = subprocess.run(
        list(map(str, argv)),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        cwd=ROOT,
    )
    if good and r.returncode:
        raise AssertionError(
            f"command failed ({r.returncode}): {argv}\n{r.stdout}{r.stderr}"
        )
    return r, time.monotonic() - started


def ok(text):
    print("PASS  " + text, flush=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixtures(work):
    cache = Path(
        os.environ.get(
            "DAWN_SQLITE_FIXTURES",
            Path.home() / ".cache/dawn/sqlite-contract/downloads",
        )
    )
    cache.mkdir(parents=True, exist_ok=True)
    for name, pin in LOCK.items():
        archive = cache / pin["url"].split("/")[-1]
        if not archive.exists():
            partial = archive.with_suffix(".part")
            run(["curl", "-fLsS", "--retry", "3", pin["url"], "-o", partial])
            if digest(partial) != pin["sha256"]:
                raise AssertionError("fixture digest mismatch: " + name)
            partial.replace(archive)
        if digest(archive) != pin["sha256"]:
            raise AssertionError("fixture digest mismatch: " + name)
        with zipfile.ZipFile(archive) as z:
            wanted = (
                [
                    p
                    for p in z.namelist()
                    if p.endswith("/libsimple.so") or p == "libsimple.so"
                ]
                if name == "simple"
                else [
                    p
                    for p in z.namelist()
                    if p.endswith(("/sqlite3.c", "/sqlite3ext.h", "/sqlite3.h"))
                ]
            )
            assert wanted, "required fixture missing: " + name
            for member in wanted:
                (work / Path(member).name).write_bytes(z.read(member))
    assert digest(work / "libsimple.so") == LOCK["simple"]["library_sha256"]
    assert digest(work / "sqlite3.c") == LOCK["sqlite"]["source_sha256"]
    ok("required fixtures: simple 0.7.1 and SQLite 3.54.1 verified; no skips")


def seed(path):
    spec = importlib.util.spec_from_file_location(
        "fixture", BACKEND / "scripts/contract_fixture.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(str(path))
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA journal_mode=WAL").fetchall()
    return path


def fresh(work, name, fixture):
    p = work / (name + ".db")
    shutil.copy2(fixture, p)
    return p


def jvm_project(work):
    project = work / "jdbc"
    (project / "src/db").mkdir(parents=True)
    shutil.copy2(BACKEND / "app/jvm/src/db/jdbc.dawn", project / "src/db/jdbc.dawn")
    manifest = (
        (BACKEND / "app/jvm/dawn.toml")
        .read_text()
        .replace(
            'site_core = "../../core"',
            f'site_core = "{BACKEND / "core"}"\nsql_scenarios = "{BACKEND / "test/sql"}"',
        )
    )
    (project / "dawn.toml").write_text(manifest)
    (project / "src/main.dawn").write_text(
        """use db/jdbc.{open_ext, with_connection}
use site_core/db/sql.{execute, query, ColStr}
use sql_scenarios/scenarios.{demand, sql_suite, fts_suite}
pub fn main() -> Unit !io = {
  let argv = args()
  let mode = argv[0]
  let path = argv[1]
  let ext = argv[2]
  let r: Result[Unit, String] = with_connection(open_ext("jdbc:sqlite:${path}"), () => {
    execute("PRAGMA busy_timeout=5000", [])?
    let _ = query("PRAGMA journal_mode=WAL", [], [ColStr])?
    if ext != "" { execute("select load_extension(?)", [PStr(ext)])? }
    if mode == "fts" { fts_suite() } else { sql_suite() }
  })
  demand(r)
}
""".replace("query, ColStr", "query, ColStr, PStr")
    )
    return project


def compare(work, native, jar, fixture, env, mode):
    ext = str(work / "libsimple.so") if mode == "fts" else ""
    outputs = []
    for arm, command in [
        ("jdbc", ["java", "-XX:-UsePerfData", "-jar", jar]),
        ("native", [native]),
    ]:
        p = fresh(work, mode + "-" + arm, fixture)
        r, elapsed = run([*command, mode, p, ext], env=env)
        outputs.append(r.stdout.splitlines())
        (work / (mode + "-" + arm + ".log")).write_text(r.stdout + r.stderr)
        ok(f"{mode} {arm}: {len(outputs[-1])} observations; wall_s={elapsed:.3f}")
    assert outputs[0] and outputs[0] == outputs[1], (
        f"{mode} SQL arms differ:\n{outputs}"
    )
    ok(f"{mode} dual arm: {len(outputs[0])} observations identical")


def mutate(work, name, changes):
    tree = work / name
    backend = tree / "backend-dawn"
    for rel in ("core", "app/native", "test/sql", "test/native"):
        shutil.copytree(BACKEND / rel, backend / rel)
    (tree / ".mutant-workdir").write_text(name)
    GUARD.require_workdir(tree, root=ROOT)
    for rel, before, after in changes:
        p = backend / rel
        if rel.startswith("sqlite-package/"):
            package = backend / "sqlite-package"
            if not package.exists():
                shutil.copytree(ARCHIVE / "packages/sqlite", package)
                manifest = backend / "app/native/dawn.toml"
                manifest.write_text(
                    manifest.read_text().split("[deps.sqlite]")[0]
                    + 'sqlite = "../../sqlite-package"\n'
                )
        text = p.read_text()
        assert text.count(before) == 1, (name, rel, text.count(before))
        p.write_text(text.replace(before, after))
    return backend / "test/native"


def controls(work, dawnc, fixture, env):
    adapter = "app/native/src/db/native_sqlite.dawn"
    core = "core/src/db/sql.dawn"
    cases = [
        (
            "swallow-wal",
            [
                (
                    adapter,
                    '_ -> return Err(ForeignError { kind = "site.db.wal", message = "SQLite did not enter WAL mode", cause = None })',
                    "_ -> ()",
                )
            ],
            "wal-error",
            ":memory:",
            "",
            "assertion: startup-wal",
        ),
        (
            "swallow-extension",
            [
                (
                    adapter,
                    'sqlite.load_extension(c, ext, Some("sqlite3_simple_init"))?',
                    'let _ = sqlite.load_extension(c, ext, Some("sqlite3_simple_init"))',
                )
            ],
            "extension-error",
            None,
            str(work / "missing.so"),
            "assertion: startup-extension",
        ),
        (
            "sql-load-open",
            [
                (
                    "sqlite-package/src/sqlite_stub.c",
                    "int rc = api.db_config(d->ptr.db, 1005, 1, NULL);",
                    'int rc = ((int (*)(sqlite3 *, int))dlsym(library, "sqlite3_enable_load_extension"))(d->ptr.db, 1);',
                )
            ],
            "gate",
            None,
            str(work / "sql_gate.so"),
            "sql load_extension was enabled during C loading",
        ),
        (
            "null-is-zero",
            [
                (adapter, "sqlite.Null -> VNull", "sqlite.Null -> VInt(0)"),
                (adapter, "VStr, VNull, QueryResult", "VStr, QueryResult"),
            ],
            "sql",
            None,
            "",
            "assertion: null-before-conversion",
        ),
        (
            "omit-panic-rollback",
            [(core, '  let _r = execute("rollback", [])\n  ()', "  ()")],
            "sql",
            None,
            "",
            "assertion: tx-panic-rollback",
        ),
        (
            "changes-as-signature",
            [
                (
                    core,
                    '  Ok(first_str_or(rows, ""))\n}',
                    '  let _ = rows\n  Ok(to_string(scalar_int("select changes()", [])?))\n}',
                )
            ],
            "sql",
            None,
            "",
            "assertion: row-sig-noop",
        ),
    ]
    for name, changes, mode, path, ext, reason in cases:
        project = mutate(work, name, changes)
        binary = work / name / "probe"
        run([dawnc, "build", "-O0", project, "-o", binary], env=env)
        p = path or fresh(work, name, fixture)
        r, _ = run([binary, mode, p, ext], env=env, good=False)
        log = r.stdout + r.stderr
        (work / (name + ".log")).write_text(log)
        assert r.returncode != 0 and reason in log, (name, r.returncode, log)
        ok(f"negative {name}: compiled and rejected by {reason}")


def effect_controls(work, dawnc, env):
    project = mutate(work, "effect-boundary", [])
    source = project.parents[1] / "app/native/src/db/native_sqlite.dawn"
    text = source.read_text()
    start = text.index("  with handle Db {")
    end = text.index("\n  body()", start)
    source.write_text(text[:start] + text[end:])
    r, _ = run([dawnc, "check", source.parent.parent.parent], env=env, good=False)
    assert (
        r.returncode != 0
        and "`with_sqlite` uses the effect `Db`" in r.stdout + r.stderr
    )
    source.write_text(
        text
        + "\npub effect Residual {\n  fn probe() -> Int\n}\n"
        + "fn missing_residual() -> Result[Int, String] !io = "
        + 'with_db("unused", "", () => Ok(probe()))\n'
    )
    r, _ = run([dawnc, "check", source.parent.parent.parent], env=env, good=False)
    assert (
        r.returncode != 0
        and "`missing_residual` uses the effect `Residual`" in r.stdout + r.stderr
    )
    source.write_text(
        text
        + "\npub effect Residual {\n  fn probe() -> Int\n}\n"
        + "fn answered_residual() -> Result[Int, String] !io = {\n"
        + "  with handle Residual { probe() => 7 }\n"
        + '  with_db("unused", "", () => Ok(probe()))\n}\n'
    )
    run([dawnc, "check", source.parent.parent.parent], env=env)
    ok(
        "effect boundary: missing handler and unhandled residual capability both rejected"
    )


def sanitized(work, dawnc, fixture, env):
    support = ARCHIVE / "scripts/sqlite-contract"
    library = work / "libsqlite3-san.so"
    cc = CACHE.engine(work / "sqlite3.c", library)
    loader = work / "loader.o"
    run([cc, *SAN, "-c", support / "loader.c", "-o", loader])
    wrapper = work / "cc-san"
    wrapper.write_text("""#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ["SITE_CC_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-sanitize-recover=undefined", "-g"]
if "-c" not in args:
    flags += [os.environ["SITE_LOADER_OBJECT"], "-Wl,--wrap=dlopen,--wrap=dlsym"]
sys.exit(subprocess.call([os.environ["SITE_REAL_CC"], *flags, *args]))
""")
    wrapper.chmod(0o755)
    current = dict(
        env,
        CC=str(wrapper),
        SITE_REAL_CC=cc,
        SITE_LOADER_OBJECT=str(loader),
        SITE_CC_LOG=str(work / "cc.log"),
        F3_SQLITE_LIBRARY=str(library),
        ASAN_OPTIONS="detect_leaks=1:halt_on_error=1",
        UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1",
    )
    project = mutate(
        work,
        "leak-finalize",
        [
            (
                "sqlite-package/src/sqlite_stub.c",
                "int rc = api.finalize(h->ptr.stmt);",
                "int rc = 0;",
            )
        ],
    )
    leaked = work / "leak-finalize/probe"
    run([dawnc, "build", "-O0", project, "-o", leaked], env=current)
    r, _ = run(
        [leaked, "bench", fresh(work, "leak-finalize", fixture), ""],
        env=current,
        good=False,
    )
    (work / "leak-finalize.log").write_text(r.stdout + r.stderr)
    assert r.returncode != 0 and "LeakSanitizer: detected memory leaks" in r.stderr
    ok("sanitizer negative: omitted finalize produces an actual LSan leak report")
    binary = work / "probe-san"
    run([dawnc, "build", "-O0", BACKEND / "test/native", "-o", binary], env=current)
    commands = [json.loads(line) for line in (work / "cc.log").read_text().splitlines()]
    assert any(
        "-c" in row and any(x.endswith("sqlite_stub.c") for x in row)
        for row in commands
    ), "SQLite stub did not pass through sanitizer compiler"
    r, elapsed = run([dawnc, "test", "-O0", BACKEND / "test/native"], env=current)
    (work / "san-tests.log").write_text(r.stdout + r.stderr)
    for mode in ("sql", "fts"):
        p = fresh(work, "san-" + mode, fixture)
        r, _ = run(
            [binary, mode, p, str(work / "libsimple.so") if mode == "fts" else ""],
            env=current,
        )
        assert "Sanitizer" not in r.stderr, r.stderr
    ok(
        f"native handler tests, SQL and FTS under ASan/UBSan/LSan: clean; test_wall_s={elapsed:.3f}"
    )


def busy(work, binary, fixture, env):
    p = fresh(work, "busy", fixture)
    holder = subprocess.Popen(
        [binary, "hold", p, ""],
        env=env,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(holder.stdout, selectors.EVENT_READ)
            assert selector.select(10), "writer never announced its lock"
        assert holder.stdout.readline().strip() == "locked"
        r, _ = run([binary, "contend", p, ""], env=env)
        ns = int(r.stdout.split("\t")[1])
        assert 100_000_000 <= ns <= 3_000_000_000, ("BUSY window", ns)
        ok(f"two native processes BUSY: {ns / 1e6:.3f} ms with timeout 150 ms")
        holder.communicate("release\n", timeout=10)
        assert holder.returncode == 0
        run([binary, "bench", p, ""], env=env)
        ok("writer lock is released after holder exits")
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.communicate()


def benchmark(work, binary, fixture, env, samples):
    measurements = {"noext": [], "simple": []}
    for i in range(samples + 10):
        for name, extension in (("noext", ""), ("simple", str(work / "libsimple.so"))):
            r, elapsed = run([binary, "bench", fixture, extension], env=env)
            if i >= 10:
                measurements[name].append([int(r.stdout) / 1e6, elapsed * 1000])
    (work / "bench.json").write_text(json.dumps(measurements, indent=2))
    for name, rows in measurements.items():
        for index, kind in enumerate(("inside", "spawn")):
            values = sorted(row[index] for row in rows)
            ok(
                f"bench {name} {kind} n={samples} ms p50={statistics.median(values):.3f} p95={values[max(0, int(len(values) * 0.95) - 1)]:.3f}"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--benchmark", type=int, default=0)
    args = parser.parse_args()
    started = time.monotonic()
    dawnc = Path(run([ROOT / "scripts/fetch-dawn-native.sh"])[0].stdout.strip())
    dawn = Path(run([ROOT / "scripts/fetch-dawn.sh"])[0].stdout.strip())
    # Both toolchain pins and the full URL package archive are verified by check.
    run([dawnc, "check", BACKEND / "app/native", BACKEND / "test/native"])
    with tempfile.TemporaryDirectory(prefix="site-sqlite-") as temporary:
        work = args.work or Path(temporary)
        work.mkdir(parents=True, exist_ok=True)
        assert not any(work.iterdir()), "work directory must be empty"
        fixtures(work)
        env = dict(
            os.environ,
            DAWN_SQLITE_TEST_DIR=str(work),
            DAWN_SQLITE_SIMPLE=str(work / "libsimple.so"),
        )
        fixture = seed(work / "fixture.db")
        support = ARCHIVE / "scripts/sqlite-contract"
        cc = shutil.which("clang-20") or shutil.which("cc")
        run(
            [
                cc,
                "-shared",
                "-fPIC",
                "-Dsqlite3_extension_init=sqlite3_simple_init",
                "-I",
                work,
                support / "sql_gate.c",
                "-o",
                work / "sql_gate.so",
            ]
        )
        # Every negative control must compile and fail for its named reason first.
        effect_controls(work, dawnc, env)
        controls(work, dawnc, fixture, env)
        native = work / "probe"
        _, elapsed = run(
            [dawnc, "build", BACKEND / "test/native", "-o", native], env=env
        )
        ok(f"native handler consumer check/build wall_s={elapsed:.3f}")
        r, elapsed = run([dawnc, "test", BACKEND / "test/native"], env=env)
        (work / "native-tests.log").write_text(r.stdout + r.stderr)
        print(r.stdout, end="", flush=True)
        ok(f"native handler test wall_s={elapsed:.3f}")
        jar = work / "jdbc.jar"
        run([dawn, "build", jvm_project(work), "-o", jar], env=env)
        compare(work, native, jar, fixture, env, "sql")
        compare(work, native, jar, fixture, env, "fts")
        run(
            [native, "gate", fresh(work, "gate", fixture), work / "sql_gate.so"],
            env=env,
        )
        ok("SQL extension loading disabled during C init and after return")
        busy(work, native, fixture, env)
        spec = importlib.util.spec_from_file_location(
            "jdbc_rollback", BACKEND / "scripts/jdbc-init/rollback.py"
        )
        rollback = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rollback)
        rollback.matrix(work / "rollback", dawn, dawnc, fixture, work, env)
        sanitized(work, dawnc, fixture, env)
        if args.benchmark:
            benchmark(work, native, fixture, env, args.benchmark)
        ok(f"native SQLite handler acceptance wall_s={time.monotonic() - started:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
