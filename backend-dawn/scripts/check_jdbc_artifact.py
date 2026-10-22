"""Reject stale or modified runtime jars and a divergent manifest class path."""

import argparse
import hashlib
import re
import zipfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DRIVER = "sqlite-jdbc-3.53.4.0.jar"
DRIVER_SHA = "bcb1f51e36f940867e83342f9efbf5968ac44a6bef4d397bb4af7b17b45cd2fb"


def check(jar: Path, lock: Path) -> dict[str, str]:
    text = lock.read_text()
    if "coord org.xerial:sqlite-jdbc:3.53.4.0\n" not in text:
        raise ValueError("JDBC coordinate is not the approved 3.53.4.0")
    artifacts = dict(
        (name, sha)
        for sha, name in re.findall(r"(?m)^artifact ([0-9a-f]{64})  (\S+)$", text)
    )
    if artifacts.get(DRIVER) != DRIVER_SHA:
        raise ValueError("JDBC lock does not contain the approved driver digest")
    library = jar.parent / "lib"
    actual = {path.name for path in library.iterdir()}
    if actual != set(artifacts):
        raise ValueError(
            f"runtime lib closure mismatch: actual={sorted(actual)}, expected={sorted(artifacts)}"
        )
    for name, sha in artifacts.items():
        path = library / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"runtime jar must be a regular file: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError(f"runtime jar digest mismatch: {name}")
    with zipfile.ZipFile(jar) as archive:
        manifest = archive.read("META-INF/MANIFEST.MF").decode().replace("\r\n", "\n")
    manifest = manifest.replace("\n ", "")
    paths = re.findall(r"(?m)^Class-Path: (.*)$", manifest)
    expected = {"lib/" + name for name in artifacts}
    if (
        len(paths) != 1
        or set(paths[0].split()) != expected
        or len(paths[0].split()) != len(expected)
    ):
        raise ValueError("manifest Class-Path differs from the locked runtime closure")
    return artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jar", type=Path)
    parser.add_argument("--lock", type=Path, default=BACKEND / "app/jvm/dawn.lock")
    args = parser.parse_args()
    for name, sha in check(args.jar.resolve(), args.lock).items():
        print(name, sha)


if __name__ == "__main__":
    main()
