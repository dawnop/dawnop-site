"""Exercise HMAC keys, account checks and cross process bcrypt leases on both arms."""

import argparse
import hmac
import importlib.util
import os
import selectors
import shutil
import sqlite3
import subprocess
import sys
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


def project(work, arm, backend=BACKEND):
    target = work / arm
    (target / "src").mkdir(parents=True)
    if arm == "jdbc":
        app = BACKEND / "app/jvm"
        (target / "src/db").mkdir()
        shutil.copy2(app / "src/db/jdbc.dawn", target / "src/db/jdbc.dawn")
        manifest = (
            (app / "dawn.toml")
            .read_text()
            .replace(
                'site_core = "../../core"',
                f'site_core = "{backend / "core"}"\nsql_scenarios = "{backend / "test/sql"}"',
            )
        )
        connection = """use db/jdbc.{open_ext, with_connection}
use site_core/db/sql.{Db, execute, query, ColStr}
fn with_db(path: String, body: fn() -> Result[Unit, String] !Db !io) -> Result[Unit, String] !io =
  with_connection(open_ext("jdbc:sqlite:${path}"), () => {
    execute("PRAGMA busy_timeout=5000", [])?
    let _ = query("PRAGMA journal_mode=WAL", [], [ColStr])?
    body()
  })
"""
    else:
        manifest = f'''schema = 1
name = "auth_probe"
[deps]
site_core = "{backend / "core"}"
site_sqlite = "{backend / "app/native"}"
sql_scenarios = "{backend / "test/sql"}"
'''
        connection = """use site_core/db/sql.{Db}
use site_sqlite/db/native_sqlite as adapter
fn with_db(path: String, body: fn() -> Result[Unit, String] !Db !io) -> Result[Unit, String] !io =
  adapter.with_db(path, "", body)
"""
    (target / "dawn.toml").write_text(manifest)
    (target / "src/main.dawn").write_text(
        """use sql_scenarios/scenarios.{demand}
use sql_scenarios/auth_scenarios.{auth_suite, auth_process}
"""
        + connection
        + """pub fn main() -> Unit !io = {
  let argv = args()
  demand(with_db(argv[1], () => {
    if len(argv) > 2 { auth_process(argv[0], argv[2]) }
    else { auth_suite(argv[0]) }
  }))
}
"""
    )
    return target


def build(work, dawnc, dawn, backend=BACKEND):
    commands = {}
    for arm in ("native", "jdbc"):
        artifact = work / ("probe" if arm == "native" else "jdbc.jar")
        compiler = dawnc if arm == "native" else dawn
        run([compiler, "build", project(work, arm, backend), "-o", artifact])
        commands[arm] = (
            [artifact]
            if arm == "native"
            else ["java", "-XX:-UsePerfData", "-Xmx96m", "-jar", artifact]
        )
    return commands


def count(path):
    with sqlite3.connect(path) as c:
        return c.execute("select count(*) from auth_test_bcrypt").fetchone()[0]


def ready(children, expected, timeout=45):
    with selectors.DefaultSelector() as selector:
        for child in children:
            selector.register(child.stdout, selectors.EVENT_READ, child)
        deadline = time.monotonic() + timeout
        pending = {child: bytearray() for child in children}
        while selector.get_map():
            remaining = deadline - time.monotonic()
            assert remaining > 0, "process barrier timed out"
            events = selector.select(remaining)
            assert events, "process barrier timed out"
            for key, _ in events:
                # Read only through the token. TextIO can buffer the next line,
                # hiding it from the selector after a leading HotSpot warning.
                # One byte also keeps partial lines within the same deadline.
                chunk = os.read(key.fd, 1)
                assert chunk, f"process exited before barrier {expected!r}"
                line = pending[key.data]
                line.extend(chunk)
                assert len(line) <= 65536, "process barrier line too long"
                if chunk != b"\n":
                    continue
                if line.rstrip(b"\r\n") == expected.encode():
                    selector.unregister(key.fileobj)
                else:
                    sys.stderr.write(line.decode(errors="replace"))
                line.clear()


def burst(commands, path, password, *, expected=1, negative=False):
    children = []
    try:
        for command in commands:
            children.append(
                subprocess.Popen(
                    list(map(str, [*command, "auth-barrier", path, password])),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        ready(children, "ready")
        started = time.monotonic()
        for child in children:
            child.stdin.write("go\n")
            child.stdin.flush()
        for child in children:
            out, err = child.communicate(timeout=25)
            assert child.returncode == 0, (out, err)
            if not negative:
                assert out.strip() == (
                    "accepted" if password == "contract-fixture-pw" else "denied"
                ), (out, err)
        total = count(path)
        if negative:
            assert total == len(commands), (
                f"mutex-count did not expose every duplicate: {total}"
            )
        else:
            assert total == expected, f"mutex-count expected {expected}, got {total}"
        return total, time.monotonic() - started
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate()


def initialized(work, name, fixture, command):
    path = SQL.fresh(work, name, fixture)
    run([*command, "auth-init", path, "unused"])
    return path


def crash_recovery(command, path):
    holder = subprocess.Popen(
        list(map(str, [*command, "auth-hold", path, "contract-fixture-pw"])),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready([holder], "holding")
        with sqlite3.connect(path, isolation_level=None) as c:
            assert (
                c.execute("select count(*) from auth_verify_leases").fetchone()[0] == 1
            )
            # Holding bcrypt work never retains SQLite's global writer lock.
            c.execute("begin immediate")
            c.execute("rollback")
        holder.kill()
        holder.communicate(timeout=5)
        result, elapsed = run(
            [*command, "auth-once", path, "contract-fixture-pw"], timeout=25
        )
        assert result.stdout.strip() == "accepted" and count(path) == 2, (
            "crash-recovery: dead owner blocked verification"
        )
        assert elapsed < 12, f"crash-recovery exceeded bound: {elapsed}"
        return elapsed
    finally:
        if holder.poll() is None:
            holder.kill()
        holder.communicate()


def controls(work, dawnc, dawn, fixture):
    source = "core/src/svc/dav_auth.dawn"
    cases = [
        (
            "plaintext-key",
            'hmac_sha256_hex(secret, "dawnop.dav.credentials.v2\\u{0}" ++ raw)',
            "raw ++ str.slice(secret, 0, 0)",
            "auth-key",
            "hmac-key",
        ),
        (
            "bare-sha-key",
            'hmac_sha256_hex(secret, "dawnop.dav.credentials.v2\\u{0}" ++ raw)',
            "sha256.hex(bytes.utf8(raw ++ str.slice(secret, 0, 0)))",
            "auth-key",
            "hmac-key",
        ),
        (
            "old-password-hit",
            "eq_const(str_at(r, 0), version(secret, int_at(r, 2), username, str_at(r, 3), str_at(r, 4)))",
            "eq_const(version(secret, int_at(r, 2), username, str_at(r, 3), str_at(r, 4)), version(secret, int_at(r, 2), username, str_at(r, 3), str_at(r, 4)))",
            "auth-account",
            "password-change-invalidates",
        ),
        (
            "expired-hit",
            "age < TTL_MS",
            "age <= TTL_MS",
            "auth-expiry",
            "auth-exact-expiry",
        ),
        (
            "no-mutex",
            "return Ok(if int_at(r, 3) == -1 { AuthDenied } else { AuthWait })",
            "return Ok(AuthOwner(a))",
            None,
            "mutex-count",
        ),
        (
            "permanent-lease",
            "str_at(r, 0) == a.version && int_at(r, 1) <= ms && ms < int_at(r, 2)",
            "str_at(r, 0) == a.version",
            None,
            "crash-recovery",
        ),
    ]
    for name, before, after, mode, reason in cases:
        changes = [(source, before, after)]
        if name == "bare-sha-key":
            changes.append(
                (
                    source,
                    "use std/str\n",
                    "use std/str\nuse std/bytes\nuse sha2/sha256\n",
                )
            )
        native_project = SQL.mutate(work, name, changes)
        commands = build(work / name, dawnc, dawn, native_project.parents[1])
        for arm, command in commands.items():
            if mode:
                path = SQL.fresh(work, name + arm, fixture)
                result, _ = run([*command, mode, path], good=False)
                log = result.stdout + result.stderr
                (work / f"{name}-{arm}.log").write_text(log)
                assert result.returncode != 0 and reason in log, (name, arm, log)
            elif name == "no-mutex":
                path = initialized(work, name + arm, fixture, command)
                total, elapsed = burst([command] * 40, path, "wrong", negative=True)
                ok(
                    f"negative {name} {arm}: bcrypt={total}; wall_s={elapsed:.3f}; red by {reason}"
                )
                continue
            else:
                path = initialized(work, name + arm, fixture, command)
                try:
                    crash_recovery(command, path)
                except AssertionError as error:
                    assert "crash-recovery" in str(error), error
                else:
                    raise AssertionError("permanent lease failed to turn red")
            ok(f"negative {name} {arm}: compiled, red by {reason}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--skip-controls", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    result, _ = run([sys.executable, HERE / "ready_control.py"])
    print(result.stdout, end="", flush=True)
    dawnc = Path(run([ROOT / "scripts/fetch-dawn-native.sh"])[0].stdout.strip())
    dawn = Path(run([ROOT / "scripts/fetch-dawn.sh"])[0].stdout.strip())
    with tempfile.TemporaryDirectory(prefix="site-auth-") as temporary:
        work = args.work or Path(temporary)
        work.mkdir(parents=True, exist_ok=True)
        assert not any(work.iterdir()), "work directory must be empty"
        fixture = SQL.seed(work / "fixture.db")
        # Independently minted with Python bcrypt, same fixed salt, production cost.
        with sqlite3.connect(fixture) as c:
            c.execute(
                "update users set password_hash=?",
                ("$2b$12$fBm/BBrDqZMMZXAiEDQ8M.atcuXIoOZ18WfMf6Uw5xhj0q8dhPUCe",),
            )
        if not args.skip_controls:
            controls(work, dawnc, dawn, fixture)
        commands = build(work, dawnc, dawn)
        observations = []
        for arm, command in commands.items():
            path = SQL.fresh(work, "suite-" + arm, fixture)
            result, elapsed = run([*command, "auth", path])
            (work / f"{arm}.log").write_text(result.stdout + result.stderr)
            observations.append(result.stdout.splitlines())
            ok(f"auth {arm}: {len(observations[-1])} groups; wall_s={elapsed:.3f}")
        assert observations[0] == observations[1] and len(observations[0]) == 7
        ok(
            "auth dual arm: seven shared account HMAC TTL transaction fencing failure and schema groups identical"
        )
        for arm in ("native", "jdbc", "mixed"):
            group = (
                [commands[arm]] * 40
                if arm != "mixed"
                else [commands["jdbc"], commands["native"]] * 20
            )
            path = initialized(work, "burst-" + arm, fixture, group[0])
            total, elapsed = burst(group, path, "contract-fixture-pw")
            ok(f"40 processes {arm}: bcrypt={total}; wall_s={elapsed:.3f}")
        for arm, command in commands.items():
            path = initialized(work, "bad-burst-" + arm, fixture, command)
            total, elapsed = burst([command] * 40, path, "wrong")
            ok(
                f"40 bad credentials {arm}: bcrypt={total}; all denied; wall_s={elapsed:.3f}"
            )
            path = initialized(work, "crash-" + arm, fixture, command)
            elapsed = crash_recovery(command, path)
            ok(
                f"killed owner {arm}: recovered; bcrypt=2 including killed owner; wall_s={elapsed:.3f}"
            )
        for writer, command in commands.items():
            for reader, other in commands.items():
                path = initialized(work, writer + "-to-" + reader, fixture, command)
                run([*command, "auth-once", path, "contract-fixture-pw"])
                with sqlite3.connect(path, isolation_level=None) as c:
                    before = c.execute("select * from runtime_cache").fetchall()
                    user = c.execute(
                        "select id,username,password_hash,created_at from users"
                    ).fetchone()
                    expected_key = hmac.new(
                        b"auth-fixture-secret",
                        b"dawnop.dav.credentials.v2\0contract-admin:contract-fixture-pw",
                        "sha256",
                    ).hexdigest()
                    expected_version = hmac.new(
                        b"auth-fixture-secret",
                        (
                            "dawnop.dav.account.v2\0" + "\0".join(map(str, user))
                        ).encode(),
                        "sha256",
                    ).hexdigest()
                    assert before[0][:3] == (
                        "dav.auth.v2",
                        expected_key,
                        expected_version,
                    ), "persisted HMAC proof differs from independent reference"
                    c.execute("begin immediate")
                    result, elapsed = run(
                        [*other, "auth-once", path, "contract-fixture-pw"], timeout=10
                    )
                    c.execute("rollback")
                    assert c.execute("select * from runtime_cache").fetchall() == before
                assert result.stdout.strip() == "accepted" and count(path) == 1
                ok(
                    f"cross {writer}-to-{reader}: readonly hit under writer lock; bcrypt=1; wall_s={elapsed:.3f}"
                )
        ok(f"auth cache acceptance wall_s={time.monotonic() - started:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
