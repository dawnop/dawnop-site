"""Build and verify the immutable native release with pinned engine provenance."""

import argparse
import ctypes
import hashlib
import importlib.util
import json
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PINS = json.loads(
    (
        HERE / "fixtures.lock.json"
        if (HERE / "fixtures.lock.json").exists()
        else HERE.parent / "native-sqlite/fixtures.lock.json"
    ).read_text()
)
FLAGS = [
    "-O2",
    "-shared",
    "-fPIC",
    "-DSQLITE_ENABLE_FTS5",
    "-DSQLITE_THREADSAFE=1",
    "-Wl,-soname,libsqlite3.so.0",
]


def run(argv, **kwargs):
    result = subprocess.run(
        list(map(str, argv)),
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
        **kwargs,
    )
    return result.stdout.strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(release, sha=None):
    manifest = json.loads((release / "manifest.json").read_text())
    if sha and manifest["commit"] != sha:
        raise ValueError("artifact commit mismatch")
    if (
        manifest["sqlite"]["version"] != "3.54.1"
        or manifest["platform"] != "linux-x86_64"
    ):
        raise ValueError("engine or platform mismatch")
    if manifest["inputs"] != PINS or manifest["sqlite"]["flags"] != FLAGS:
        raise ValueError("unrecognized engine build provenance")
    actual = {str(p.relative_to(release)) for p in release.rglob("*") if not p.is_dir()}
    if actual != set(manifest["files"]) | {"manifest.json"}:
        raise ValueError("artifact file set mismatch")
    for name, pin in manifest["files"].items():
        path = release / name
        if (
            path.is_symlink()
            or not path.is_file()
            or digest(path) != pin["sha256"]
            or path.stat().st_size != pin["bytes"]
        ):
            raise ValueError("artifact file digest mismatch: " + name)
    if (
        manifest["files"]["extensions/libsimple.so"]["sha256"]
        != PINS["simple"]["library_sha256"]
    ):
        raise ValueError("simple digest mismatch")
    return manifest


def build(output, work, sha):
    if output.exists() or work.exists():
        raise ValueError("output and work must be new directories")
    work.mkdir(parents=True)
    for directory in ("lib", "extensions", "deploy", "tools"):
        (output / directory).mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location(
        "sqlite_fixtures", HERE.parent / "native-sqlite/run.py"
    )
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    fixtures.fixtures(work)
    run(
        [
            "cc",
            *FLAGS,
            work / "sqlite3.c",
            "-o",
            output / "lib/libsqlite3.so.0",
            "-lm",
            "-ldl",
            "-pthread",
        ]
    )
    shutil.copy2(work / "libsimple.so", output / "extensions/libsimple.so")
    engine = ctypes.CDLL(str(output / "lib/libsqlite3.so.0"))
    engine.sqlite3_sourceid.restype = ctypes.c_char_p
    source_id = engine.sqlite3_sourceid().decode()
    project = work / "app"
    shutil.copytree(ROOT / "backend-dawn/app/native", project)
    manifest = (
        (project / "dawn.toml")
        .read_text()
        .replace(
            'site_core = "../../core"', f'site_core = "{ROOT / "backend-dawn/core"}"'
        )
    )
    manifest = manifest.replace(
        'sources = ["src/wire/finish.c"]',
        'sources = ["src/wire/finish.c", "src/package_guard.c"]',
    )
    (project / "dawn.toml").write_text(manifest)
    shutil.copy2(HERE / "guard.c", project / "src/package_guard.c")
    (project / "src/package_pin.h").write_text(
        "#define PACKAGE_SQLITE_SOURCE_ID " + json.dumps(source_id) + "\n"
    )
    dawnc = run([ROOT / "scripts/fetch-dawn-native.sh"])
    run([dawnc, "build", project, "-o", output / "dawnop-native"])
    for path in (ROOT / "backend-dawn/deploy").glob("dawnop-native*"):
        shutil.copy2(path, output / "deploy" / path.name)
    for name in ("deploy-native.sh", "deploy_native.py"):
        shutil.copy2(ROOT / "backend-dawn/deploy" / name, output / "deploy" / name)
    shutil.copy2(HERE / "package.py", output / "tools/package.py")
    (output / "tools/fixtures.lock.json").write_text(json.dumps(PINS, indent=2) + "\n")
    versions = {}
    for relative in ("dawnop-native", "lib/libsqlite3.so.0", "extensions/libsimple.so"):
        symbols = run(["readelf", "--version-info", output / relative])
        needed = sorted(
            set(re.findall(r"GLIBC_(\d+\.\d+)", symbols)),
            key=lambda s: tuple(map(int, s.split("."))),
        )
        versions[relative] = needed[-1]
        if tuple(map(int, needed[-1].split("."))) > (2, 35):
            raise ValueError("artifact requires newer glibc than jammy: " + relative)
    files = {
        str(p.relative_to(output)): {"sha256": digest(p), "bytes": p.stat().st_size}
        for p in sorted(output.rglob("*"))
        if p.is_file()
    }
    result = {
        "schema": 1,
        "commit": sha,
        "toolchain": "v0.100.0",
        "platform": "linux-x86_64",
        "build_platform": platform.platform(),
        "glibc_floor": versions,
        "glibc_maximum": "2.35",
        "cc": run(["cc", "--version"]).splitlines()[0],
        "inputs": PINS,
        "sqlite": {
            "version": "3.54.1",
            "source_id": source_id,
            "flags": FLAGS,
            "link_flags": ["-lm", "-ldl", "-pthread"],
            "extension_loading": True,
        },
        "files": files,
    }
    (output / "manifest.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    verify(output, sha)
    probe = subprocess.run(
        [str(output / "dawnop-native"), "--engine-info"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert (
        not probe.stdout and f"sqlite_path={output}/lib/libsqlite3.so.0" in probe.stderr
    )
    print(probe.stderr.strip(), flush=True)
    print(
        f"PASS native artifact: {len(files)} files, private SQLite 3.54.1 and simple 0.7.1",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "verify"])
    parser.add_argument("release", type=Path)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--sha")
    args = parser.parse_args()
    if args.command == "verify":
        verify(args.release.resolve(), args.sha)
        print("PASS complete native artifact digests and provenance")
    else:
        sha = args.sha or run(["git", "rev-parse", "HEAD"], cwd=ROOT)
        build(
            args.release.resolve(), args.work or Path(tempfile.mkdtemp()) / "build", sha
        )


if __name__ == "__main__":
    # The deployed verifier is self contained; build retains the source layout.
    main()
