"""Build the checksum-pinned synthetic SQLite engine without installing it."""

import argparse
import importlib.util
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    args = parser.parse_args()
    args.work.mkdir(mode=0o700)
    script = Path(__file__).parent / "native-sqlite/run.py"
    spec = importlib.util.spec_from_file_location("sqlite_acceptance", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.fixtures(args.work)
    subprocess.run(
        [
            "cc",
            "-shared",
            "-fPIC",
            "-O2",
            "-DSQLITE_ENABLE_FTS5",
            "-DSQLITE_THREADSAFE=1",
            str(args.work / "sqlite3.c"),
            "-o",
            str(args.work / "libsqlite3.so.0"),
            "-lm",
            "-ldl",
            "-pthread",
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
