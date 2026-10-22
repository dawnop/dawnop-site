"""Require persistent, read only cache hits on the real JDBC and native arms."""

import argparse
import importlib.util
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
ROOT = BACKEND.parent
SPEC = importlib.util.spec_from_file_location(
    "native_sqlite_acceptance", HERE.parent / "native-sqlite/run.py"
)
SQL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SQL)
run, ok = SQL.run, SQL.ok


def jvm_project(work, backend=BACKEND):
    project = work / "jdbc"
    (project / "src").mkdir(parents=True)
    app = backend / "app/jvm" if (backend / "app/jvm").exists() else BACKEND / "app/jvm"
    for rel in ("db/jdbc.dawn", "db/db.dawn", "util/ttl.dawn", "util/clock.dawn"):
        target = project / "src" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(app / "src" / rel, target)
    manifest = (
        (app / "dawn.toml")
        .read_text()
        .replace(
            'site_core = "../../core"',
            f'site_core = "{backend / "core"}"\nsql_scenarios = "{backend / "test/sql"}"',
        )
    )
    (project / "dawn.toml").write_text(manifest)
    (project / "src/main.dawn").write_text("""use std/str
use db/db.{with_db}
use util/ttl.{new_cell, cache_get, cache_put, init_cache}
use sql_scenarios/scenarios.{demand, check}
use sql_scenarios/cache_scenarios.{cache_suite, cache_write, cache_read}
pub fn main() -> Unit !io = {
  let argv = args()
  let mode = argv[0]
  let path = argv[1]
  if mode == "cache-write-now" {
    init_cache(path)
    for ns in ["monitor.v1", "respack.v1", "space.v1", "dav.v1", "cpu.v1"] {
      let cell = new_cell(path, ns)
      cache_put(cell, "persisted|${ns}")
      check(cache_get(cell, 120) == Some("persisted|${ns}"), "process-local-hit")
    }
    println("cache\\tprocess-A-wrote-five-slots")
  } else if mode == "cache-read-now" {
    for (ns, ttl) in [("monitor.v1", 120), ("respack.v1", 300), ("space.v1", 600), ("dav.v1", 300), ("cpu.v1", 300)] {
      check(cache_get(new_cell(path, ns), ttl) == Some("persisted|${ns}"), "process-restart-hit")
    }
    println("cache\\tprocess-B-read-five-slots")
  } else {
    demand(with_db(path, "", () => {
      if mode == "cache-write" { cache_write() }
      else if mode == "cache-read" { cache_read() }
      else if str.starts_with(mode, "cache") { cache_suite(mode) }
      else { panic("unknown cache mode") }
    }))
  }
}
""")
    return project


def snapshot(path):
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "select * from runtime_cache order by namespace,cache_key"
        ).fetchall()


def cross(work, commands, fixture, env):
    # The writer exits before the reader starts. Both same-arm and mixed-arm
    # pairs use the actual JVM production wrapper and native core consumer.
    for writer in commands:
        for reader in commands:
            label = writer + "-to-" + reader
            path = SQL.fresh(work, label, fixture)
            run([*commands[writer], "cache-write-now", path], env=env)
            before = snapshot(path)
            assert len(before) == 5
            result, elapsed = run([*commands[reader], "cache-read-now", path], env=env)
            assert "process-B-read-five-slots" in result.stdout
            assert snapshot(path) == before, "hit refreshed persisted timestamps"
            ok(f"two new processes {label}: five hits; wall_s={elapsed:.3f}")
    # A live SQLite writer must not prevent hits. No sleeps: hold the lock in
    # this process until the child has actually finished reading every slot.
    for reader, command in commands.items():
        path = work / "jdbc-to-native.db"
        with sqlite3.connect(path, isolation_level=None) as holder:
            holder.execute("begin immediate")
            result, elapsed = run(
                [*command, "cache-read-now", path], env=env, timeout=10
            )
            assert "process-B-read-five-slots" in result.stdout
            holder.execute("rollback")
        ok(f"{reader} hits under another process writer lock; wall_s={elapsed:.3f}")


def controls(work, dawnc, dawn, fixture, env):
    source = "core/src/util/cache.dawn"
    cases = [
        (
            "ttl-inclusive",
            "age < ttl_s * 1000",
            "age <= ttl_s * 1000",
            "cache-boundary",
            "ttl-exact-expiry",
        ),
        (
            "namespace-alias",
            "PStr(cell.namespace), PStr(cell.key)], [ColStr, ColInt]",
            'PStr("monitor.v1"), PStr(cell.key)], [ColStr, ColInt]',
            "cache-namespaces",
            "namespace-isolation",
        ),
        (
            "clock-backwards",
            "now >= created && ttl_s > 0",
            "ttl_s > 0",
            "cache-backwards",
            "clock-backwards",
        ),
        (
            "hit-writes",
            "      let created = int_at(row, 1)",
            '      execute("update runtime_cache set payload=payload where namespace=? and cache_key=?", [PStr(cell.namespace), PStr(cell.key)])?\n      let created = int_at(row, 1)',
            "cache-readonly",
            "hit-readonly",
        ),
        (
            "cpu-other-boot",
            'boot != "" && len(parts) == 2 && parts[0] == boot',
            'boot != "" && len(parts) == 2',
            "cache-cpu",
            "cpu-other-boot",
        ),
    ]
    for name, before, after, mode, reason in cases:
        project = SQL.mutate(work, name, [(source, before, after)])
        # Both handlers must expose every portable regression, after successful
        # builds; arbitrary compiler or runtime failures are never accepted.
        binary = work / name / "probe"
        run([dawnc, "build", "-O0", project, "-o", binary], env=env)
        jar = work / name / "jdbc.jar"
        run(
            [dawn, "build", jvm_project(work / name, project.parents[1]), "-o", jar],
            env=env,
        )
        for arm, command in [("native", [binary]), ("jdbc", ["java", "-jar", jar])]:
            path = SQL.fresh(work, name + arm, fixture)
            result, _ = run([*command, mode, path], env=env, good=False)
            log = result.stdout + result.stderr
            (work / f"{name}-{arm}.log").write_text(log)
            assert result.returncode != 0 and reason in log, (name, arm, log)
            ok(f"negative {name} {arm}: compiled, red by {reason}")
    # Actually restore an AtomicReference cell on the JVM, then require a fresh
    # process to see it. The writer first proves its local put/get works.
    project = SQL.mutate(work, "process-memory", [])
    backend = project.parents[1]
    shutil.copytree(BACKEND / "app/jvm", backend / "app/jvm")
    (
        backend / "app/jvm/src/util/ttl.dawn"
    ).write_text("""use java "java.util.concurrent.atomic.AtomicReference"
use java "java.lang.String"
pub opaque type TtlCell = AtomicReference
fn atomic(cell: TtlCell) -> AtomicReference = cell
pub fn new_cell(_path: String, _namespace: String) -> TtlCell !io = AtomicReference.new("")
pub fn init_cache(_path: String) -> Unit = ()
pub fn cache_put(cell: TtlCell, payload: String) -> Unit !io = atomic(cell).set(payload)
pub fn cache_get(cell: TtlCell, _ttl: Int) -> Option[String] !io = {
  let s = String.valueOf(atomic(cell).get()!)!
  if s == "" { None } else { Some(s) }
}
""")
    jar = work / "process-memory/jdbc.jar"
    run(
        [dawn, "build", jvm_project(work / "process-memory", backend), "-o", jar],
        env=env,
    )
    path = SQL.fresh(work, "process-memory", fixture)
    run(["java", "-jar", jar, "cache-write-now", path], env=env)
    result, _ = run(["java", "-jar", jar, "cache-read-now", path], env=env, good=False)
    log = result.stdout + result.stderr
    (work / "process-memory.log").write_text(log)
    assert result.returncode != 0 and "process-restart-hit" in log, log
    ok(
        "negative process-memory: local hit green, fresh process red by process-restart-hit"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    dawnc = Path(run([ROOT / "scripts/fetch-dawn-native.sh"])[0].stdout.strip())
    dawn = Path(run([ROOT / "scripts/fetch-dawn.sh"])[0].stdout.strip())
    with tempfile.TemporaryDirectory(prefix="site-cache-") as temporary:
        work = args.work or Path(temporary)
        work.mkdir(parents=True, exist_ok=True)
        assert not any(work.iterdir()), "work directory must be empty"
        env = dict(os.environ)
        fixture = SQL.seed(work / "fixture.db")
        controls(work, dawnc, dawn, fixture, env)
        native = work / "probe"
        run([dawnc, "check", BACKEND / "test/native"], env=env)
        run([dawnc, "build", BACKEND / "test/native", "-o", native], env=env)
        jar = work / "jdbc.jar"
        run([dawn, "build", jvm_project(work), "-o", jar], env=env)
        commands = {"jdbc": ["java", "-jar", jar], "native": [native]}
        observations = []
        for arm, command in commands.items():
            path = SQL.fresh(work, "suite-" + arm, fixture)
            result, elapsed = run([*command, "cache", path], env=env)
            (work / f"{arm}.log").write_text(result.stdout + result.stderr)
            observations.append(result.stdout.splitlines())
            ok(f"cache {arm}: {len(observations[-1])} groups; wall_s={elapsed:.3f}")
        assert observations[0] == observations[1] and len(observations[0]) == 7
        ok("cache dual arm: seven fake-clock schema and transaction groups identical")
        cross(work, commands, fixture, env)
        ok(f"TTL cache acceptance wall_s={time.monotonic() - started:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
