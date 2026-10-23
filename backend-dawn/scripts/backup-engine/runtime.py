"""Build real maintenance entries with isolated, digest verified SQLite engines."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "backend-dawn/scripts"


def run(argv, **kwargs):
    result = subprocess.run(
        list(map(str, argv)),
        capture_output=True,
        text=True,
        timeout=300,
        cwd=ROOT,
        **kwargs,
    )
    if result.returncode:
        raise AssertionError(f"{argv}\n{result.stdout}{result.stderr}")
    return result


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def build_entries(backend, work):
    work.mkdir(parents=True, exist_ok=True)
    dawn = run([ROOT / "scripts/fetch-dawn.sh"]).stdout.strip()
    dawnc = run([ROOT / "scripts/fetch-dawn-native.sh"]).stdout.strip()
    jar, binary = work / "backend-dawn.jar", work / "site-native"
    run([dawn, "build", backend / "app/jvm", "-o", jar])
    run([dawnc, "build", backend / "app/native", "-o", binary])
    # A real obsolete JDBC engine, with the same production entry and core.
    old = work / "old-jvm"
    shutil.copytree(backend / "app/jvm/src", old / "src")
    manifest = (
        (backend / "app/jvm/dawn.toml")
        .read_text()
        .replace('site_core = "../../core"', f'site_core = "{backend / "core"}"')
        .replace("3.53.4.0", "3.36.0.3")
    )
    (old / "dawn.toml").write_text(manifest)
    oldjar = old / "backend-dawn.jar"
    run([dawn, "build", old, "-o", oldjar])
    return {
        "BACKUP_JAR": str(jar),
        "BACKUP_NATIVE": str(binary),
        "BACKUP_OLD_JAR": str(oldjar),
    }


def prepare(work):
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    fixture = module("backup_sqlite_fixtures", SCRIPTS / "native-sqlite/run.py")
    fixture.fixtures(work)
    library = work / "libsqlite3.so.0"
    run(
        [
            "cc",
            "-O0",
            "-shared",
            "-fPIC",
            "-DSQLITE_ENABLE_FTS5",
            work / "sqlite3.c",
            "-lm",
            "-o",
            library,
        ]
    )
    # The compiler package is itself pinned by the application's dependency hash.
    pin = json.loads(
        (fixture.ARCHIVE / "scripts/sqlite-contract/fixtures.lock.json").read_text()
    )["baseline"]
    archive = work / "baseline.zip"
    run(["curl", "-fLsS", "--retry", "3", pin["url"], "-o", archive])
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == pin["sha256"]
    old = work / "old-native"
    old.mkdir()
    with zipfile.ZipFile(archive) as source:
        member = next(p for p in source.namelist() if p.endswith("/sqlite3.c"))
        raw = source.read(member)
    assert hashlib.sha256(raw).hexdigest() == pin["source_sha256"]
    (old / "sqlite3.c").write_bytes(raw)
    run(
        [
            "cc",
            "-O0",
            "-shared",
            "-fPIC",
            "-DSQLITE_ENABLE_FTS5",
            old / "sqlite3.c",
            "-lm",
            "-o",
            old / "libsqlite3.so.0",
        ]
    )
    env = {
        **build_entries(ROOT / "backend-dawn", work / "entries"),
        "LD_LIBRARY_PATH": str(work),
        "DAWN_SIMPLE_EXT": "",
        "BACKUP_OLD_NATIVE_LIBRARY": str(old),
        "BACKUP_SIMPLE_FIXTURE": str(work / "libsimple.so"),
    }
    (work / "runtime.json").write_text(json.dumps(env, indent=2) + "\n")
    return env


def configured(work):
    saved = os.environ.get("BACKUP_TEST_RUNTIME")
    return json.loads(Path(saved).read_text()) if saved else prepare(work)
