#!/usr/bin/env python3
"""Keep selected Java FFI imports inside their owning Dawn module."""

import argparse
import re
from pathlib import Path

ASSERTION = "ffi.java-net-http-confined"
OWNER = Path("src/util/http.dawn")
JAVA_NET_HTTP = re.compile(r'^\s*use java "java\.net\.http(?:\.|\")')


def violations(root: Path) -> list[tuple[Path, int, str]]:
    found: list[tuple[Path, int, str]] = []
    for source in sorted((root / "src").rglob("*.dawn")):
        relative = source.relative_to(root)
        if relative == OWNER:
            continue
        for line_number, line in enumerate(
            source.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if JAVA_NET_HTTP.match(line):
                found.append((relative, line_number, line.strip()))
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="backend-dawn project root",
    )
    args = parser.parse_args()

    found = violations(args.root.resolve())
    if not found:
        print(f"PASS  {ASSERTION}")
        return 0

    print(f"FAIL  {ASSERTION}")
    for path, line_number, line in found:
        print(f"  {path}:{line_number}: {line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
