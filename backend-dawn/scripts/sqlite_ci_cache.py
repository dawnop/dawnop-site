"""Cache only verified fixture inputs and the fully instrumented SQLite engine."""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK = HERE / "native-sqlite/fixtures.lock.json"
SAN = [
    "-fsanitize=address,undefined",
    "-fno-omit-frame-pointer",
    "-fno-sanitize-recover=undefined",
    "-g",
]
FLAGS = ["-std=c11", "-O1", "-shared", "-fPIC", "-DSQLITE_ENABLE_FTS5", *SAN]
CACHE = Path.home() / ".cache/dawn/sqlite-contract/engines"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compiler():
    cc = shutil.which("clang-20") or shutil.which("clang")
    assert cc, "clang is required for full sanitizer instrumentation"
    return cc


def identity(cc):
    pin = json.loads(LOCK.read_text())["sqlite"]
    return {
        "source_sha256": pin["source_sha256"],
        "archive_sha256": pin["sha256"],
        "flags": [*FLAGS, "-lm"],
        "compiler": subprocess.check_output([cc, "--version"], text=True),
        "compiler_sha256": digest(Path(cc).resolve()),
        "system": platform.system(),
        "machine": platform.machine(),
        "libc": list(platform.libc_ver()),
        "os_release": Path("/etc/os-release").read_text(),
        "implementation_sha256": digest(Path(__file__)),
    }


def key(inputs):
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def engine(source, destination):
    started = time.monotonic()
    cc = compiler()
    inputs = identity(cc)
    assert digest(source) == inputs["source_sha256"], "SQLite source digest mismatch"
    entry = CACHE / key(inputs)
    library = entry / "libsqlite3-san.so"
    manifest = entry / "manifest.json"
    if entry.exists():
        recorded = json.loads(manifest.read_text())
        assert recorded["inputs"] == inputs, "engine inputs mismatch"
        assert digest(library) == recorded["sha256"], "san engine digest mismatch"
        state = "hit"
    else:
        CACHE.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="build-", dir=CACHE) as temporary:
            build = Path(temporary)
            output = build / library.name
            subprocess.run(
                [cc, *FLAGS, str(source), "-lm", "-o", str(output)],
                check=True,
                timeout=300,
            )
            (build / manifest.name).write_text(
                json.dumps({"inputs": inputs, "sha256": digest(output)}, sort_keys=True)
            )
            build.rename(entry)
        state = "miss cold build"
    shutil.copy2(library, destination)
    print(
        f"PASS  sanitized SQLite engine cache {state}: key={key(inputs)} "
        f"wall_s={time.monotonic() - started:.3f}",
        flush=True,
    )
    return cc


def main():
    values = {
        "fixtures_key": f"sqlite-fixtures-{platform.system()}-{platform.machine()}-{digest(LOCK)}",
        "engine_key": f"sqlite-san-{platform.system()}-{platform.machine()}-{key(identity(compiler()))}",
    }
    for name, value in values.items():
        print(f"{name}={value}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            for name, value in values.items():
                output.write(f"{name}={value}\n")


if __name__ == "__main__":
    main()
