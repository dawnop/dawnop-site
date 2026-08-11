#!/usr/bin/env python3
"""Apply one compiling WebDAV dot segment mutant in place."""

import argparse
from pathlib import Path

ANCHOR = """fn is_dot_segment(s: String) -> Bool =
  s == "." || s == ".."
"""

MUTANTS = {
    "drop-dot-check": """fn is_dot_segment(s: String) -> Bool =
  s == ".."
""",
    "drop-dotdot-check": """fn is_dot_segment(s: String) -> Bool =
  s == "."
""",
    "reject-dot-containing-names": """fn is_dot_segment(s: String) -> Bool =
  str.contains(s, ".")
""",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mutant", choices=sorted(MUTANTS))
    parser.add_argument("source", type=Path)
    args = parser.parse_args()

    text = args.source.read_text(encoding="utf-8")
    count = text.count(ANCHOR)
    if count != 1:
        parser.error(f"expected one is_dot_segment anchor, found {count}")
    args.source.write_text(text.replace(ANCHOR, MUTANTS[args.mutant]), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
