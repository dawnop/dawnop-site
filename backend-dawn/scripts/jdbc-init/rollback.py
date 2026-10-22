"""Run a local rollback matrix with pinned engines and real application adapters."""

import ast
import hashlib
import os
import selectors
import shutil
import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def run(argv, env):
    r = subprocess.run(
        list(map(str, argv)), env=env, text=True, capture_output=True, timeout=180
    )
    if r.returncode:
        raise AssertionError(f"{argv}\n{r.stdout}{r.stderr}")
    return r


def matrix(work, dawn, dawnc, fixture, fixtures, env):
    work.mkdir()
    library = work / "engine"
    library.mkdir()
    # This is only a local isolated fixture, not S12 packaging or a system install.
    run(
        [
            shutil.which("cc"),
            "-O2",
            "-shared",
            "-fPIC",
            "-DSQLITE_ENABLE_FTS5",
            "-DSQLITE_THREADSAFE=1",
            "-Wl,-soname,libsqlite3.so.0",
            fixtures / "sqlite3.c",
            "-lm",
            "-ldl",
            "-pthread",
            "-o",
            library / "libsqlite3.so.0",
        ],
        env,
    )
    current = dict(env, LD_LIBRARY_PATH=str(library))
    manifest = (
        (BACKEND / "app/jvm/dawn.toml")
        .read_text()
        .replace(
            'site_core = "../../core"',
            f'site_core = "{BACKEND / "core"}"\nsql_scenarios = "{BACKEND / "test/sql"}"',
        )
    )
    for arm in ("jvm", "native"):
        project = work / arm
        (project / "src").mkdir(parents=True)
        if arm == "jvm":
            shutil.copytree(BACKEND / "app/jvm/src/db", project / "src/db")
            (project / "dawn.toml").write_text(manifest)
            shutil.copy2(BACKEND / "app/jvm/dawn.lock", project / "dawn.lock")
            body = "with_db(a[1], a[2], () => rollback(a[0]))"
            imports = "use db/db.{with_db}"
        else:
            (project / "dawn.toml").write_text(
                'schema = 1\nname = "rollback_probe"\n[deps]\n'
                + f'site_sqlite = "{BACKEND / "app/native"}"\nsite_core = "{BACKEND / "core"}"\nsql_scenarios = "{BACKEND / "test/sql"}"\n'
            )
            body = "let r: Result[Unit, String] = with_db(a[1], a[2], () => { rollback(a[0])\n Ok(()) })\n match r { Ok(_) -> ()\n Err(m) -> panic(m) }"
            imports = "use site_sqlite/db/native_sqlite.{with_db}"
        (project / "src/main.dawn").write_text(
            imports
            + "\nuse sql_scenarios/rollback_scenarios.{rollback}\npub fn main() -> Unit !io = {\n let a = args()\n "
            + body
            + "\n}\n"
        )
        run(
            [
                dawn if arm == "jvm" else dawnc,
                "build",
                project,
                "-o",
                work / ("probe.jar" if arm == "jvm" else "probe"),
            ],
            current,
        )
    # Read the existing deployed trigger definitions without importing the frozen backend.
    tree = ast.parse((BACKEND.parent / "backend/app/core/search.py").read_text())
    triggers = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "_CREATE_TRIGGERS" for t in node.targets
        )
    )
    # Seed via the pinned native engine, including real production article schema.
    import ctypes

    lib = ctypes.CDLL(str(library / "libsqlite3.so.0"))
    lib.sqlite3_open.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.sqlite3_exec.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    lib.sqlite3_enable_load_extension.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.sqlite3_load_extension.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_void_p,
    ]
    lib.sqlite3_close.argtypes = [ctypes.c_void_p]
    results = []
    for case in ("live-wal", "closed-reopen", "committed-crash", "uncommitted-crash"):
        db = work / (case + ".db")
        shutil.copy2(fixture, db)
        c = ctypes.c_void_p()
        assert lib.sqlite3_open(os.fsencode(db), ctypes.byref(c)) == 0
        try:
            assert lib.sqlite3_enable_load_extension(c, 1) == 0
            assert (
                lib.sqlite3_load_extension(
                    c,
                    os.fsencode(fixtures / "libsimple.so"),
                    b"sqlite3_simple_init",
                    None,
                )
                == 0
            )
            assert lib.sqlite3_enable_load_extension(c, 0) == 0
            sql = (
                "DROP TABLE article_fts; CREATE VIRTUAL TABLE article_fts USING fts5(title,summary,content,content='articles',content_rowid='id',tokenize='simple'); INSERT INTO article_fts(article_fts) VALUES('rebuild');"
                + ";".join(triggers)
            )
            assert lib.sqlite3_exec(c, sql.encode(), None, None, None) == 0
        finally:
            assert lib.sqlite3_close(c) == 0
        holder = subprocess.Popen(
            [
                str(work / "probe"),
                "seed-rollback" if case == "uncommitted-crash" else "seed",
                str(db),
                str(fixtures / "libsimple.so"),
            ],
            env=current,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(holder.stdout, selectors.EVENT_READ)
                assert selector.select(15), "native seed never ready"
            assert holder.stdout.readline().strip() == "ready"
            wal_bytes = Path(str(db) + "-wal").stat().st_size
            assert wal_bytes > 0
            if case == "closed-reopen":
                out, err = holder.communicate("release\n", timeout=15)
                assert holder.returncode == 0, out + err
                (work / (case + "-seed.log")).write_text(err)
            if case.endswith("crash"):
                holder.kill()
                out, err = holder.communicate(timeout=15)
                assert holder.returncode < 0
                (work / (case + "-seed.log")).write_text(err)
            r = run(
                [
                    "java",
                    "-jar",
                    work / "probe.jar",
                    "jvm",
                    db,
                    fixtures / "libsimple.so",
                ],
                current,
            )
            (work / (case + "-jvm.log")).write_text(r.stdout + r.stderr)
            if holder.poll() is None:
                out, err = holder.communicate("release\n", timeout=15)
                assert holder.returncode == 0, out + err
                (work / (case + "-seed.log")).write_text(err)
            r = run([work / "probe", "verify", db, fixtures / "libsimple.so"], current)
            (work / (case + "-native.log")).write_text(r.stdout + r.stderr)
            results.append(
                f"{case}: native 3.54.1 write -> JDBC 3.53.4 read/write -> native read/write; WAL before handoff={wal_bytes}; article insert/update/delete and paste insert/delete; integrity ok; FTS secure-delete=0"
            )
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.communicate()
    (work / "matrix.txt").write_text("\n".join(results) + "\n")
    (work / "engine.sha256").write_text(
        hashlib.sha256((library / "libsqlite3.so.0").read_bytes()).hexdigest()
    )
    for result in results:
        print("PASS rollback " + result, flush=True)
