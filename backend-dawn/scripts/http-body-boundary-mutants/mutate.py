#!/usr/bin/env python3
"""Apply one compiling HTTP request body boundary mutant in place."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mutant_workdir import require_workdir  # noqa: E402


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mutant",
        choices=("drop-upload-prefix", "leak-java-net-http", "swap-file-tail"),
    )
    parser.add_argument("project", type=Path)
    args = parser.parse_args()

    project = require_workdir(args.project)

    http = project / "src/util/http.dawn"
    qiniu = project / "src/qiniu/rs.dawn"
    if args.mutant == "swap-file-tail":
        replace_once(
            http,
            "    BodyPublishers.concat(h, f, t)!\n",
            "    BodyPublishers.concat(h, t, f)!\n",
        )
    elif args.mutant == "drop-upload-prefix":
        replace_once(
            qiniu,
            '    Err(e) -> Err("upload_file: $e")\n',
            "    Err(e) -> Err(e)\n",
        )
    else:
        replace_once(
            qiniu,
            "use std/map\n",
            'use std/map\nuse java "java.net.http.HttpRequest.BodyPublisher"\n',
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
