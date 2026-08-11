#!/usr/bin/env python3
"""Keep selected Java FFI types inside their owning Dawn modules."""

import argparse
import re
from pathlib import Path

HTTP_ASSERTION = "ffi.java-net-http-confined"
HTTP_OWNER = Path("src/util/http.dawn")
JAVA_NET_HTTP = re.compile(r'^\s*use java "java\.net\.http(?:\.|\")')
SQL_ASSERTION = "ffi.java-sql-confined"
SQL_OWNER = Path("src/db/sql.dawn")
JAVA_SQL = re.compile(r'^\s*use java "java\.sql(?:\.|\")')
OPAQUE_ASSERTION = "ffi.db-conn-opaque"
OPAQUE_DECLARATION = "pub opaque type DbConn = Connection"
RAW_ASSERTION = "ffi.db-conn-raw-confined"
CONNECTION_TOKEN = re.compile(r"\bConnection\b")
CONNECTION_IMPORT = 'use java "java.sql.Connection"'
DB_CONN_REPRESENTATION = re.compile(
    r"^(?:pub opaque type|pub alias) DbConn = Connection$"
)
RAW_DECLARATION = "fn raw(c: DbConn) -> Connection = c"


def import_violations(
    root: Path, owner: Path, pattern: re.Pattern[str]
) -> list[tuple[Path, int, str]]:
    found: list[tuple[Path, int, str]] = []
    for source in sorted((root / "src").rglob("*.dawn")):
        relative = source.relative_to(root)
        if relative == owner:
            continue
        for line_number, line in enumerate(
            source.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if pattern.match(line):
                found.append((relative, line_number, line.strip()))
    return found


def report_import_assertion(
    assertion: str, violations: list[tuple[Path, int, str]]
) -> bool:
    if not violations:
        print(f"PASS  {assertion}")
        return True

    print(f"FAIL  {assertion}")
    for path, line_number, line in violations:
        print(f"  {path}:{line_number}: {line}")
    return False


def report_opaque_assertion(root: Path) -> bool:
    owner = root / SQL_OWNER
    matches = [
        line
        for line in owner.read_text(encoding="utf-8").splitlines()
        if line.strip() == OPAQUE_DECLARATION
    ]
    if len(matches) == 1:
        print(f"PASS  {OPAQUE_ASSERTION}")
        return True

    print(f"FAIL  {OPAQUE_ASSERTION}")
    print(
        f"  {SQL_OWNER}: expected exactly `{OPAQUE_DECLARATION}`, found {len(matches)}"
    )
    return False


def connection_seam(line: str) -> str | None:
    stripped = line.strip()
    if stripped == CONNECTION_IMPORT:
        return "java.sql import"
    if DB_CONN_REPRESENTATION.fullmatch(stripped):
        return "DbConn representation"
    if stripped == RAW_DECLARATION:
        return "private raw accessor"
    return None


def report_raw_connection_assertion(root: Path) -> bool:
    owner = root / SQL_OWNER
    seen = {
        "java.sql import": 0,
        "DbConn representation": 0,
        "private raw accessor": 0,
    }
    violations: list[tuple[int, str]] = []
    for line_number, line in enumerate(
        owner.read_text(encoding="utf-8").splitlines(), start=1
    ):
        matches = list(CONNECTION_TOKEN.finditer(line))
        if not matches:
            continue
        seam = connection_seam(line)
        if seam is None or len(matches) != 1:
            violations.append((line_number, line.strip()))
            continue
        seen[seam] += 1

    missing_or_duplicate = [name for name, count in seen.items() if count != 1]
    if not violations and not missing_or_duplicate:
        print(f"PASS  {RAW_ASSERTION}")
        return True

    print(f"FAIL  {RAW_ASSERTION}")
    for line_number, line in violations:
        print(f"  {SQL_OWNER}:{line_number}: unapproved Connection token: {line}")
    for name in missing_or_duplicate:
        print(f"  {SQL_OWNER}: expected exactly one {name}, found {seen[name]}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="backend-dawn project root",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    results = [
        report_import_assertion(
            HTTP_ASSERTION,
            import_violations(root, HTTP_OWNER, JAVA_NET_HTTP),
        ),
        report_import_assertion(
            SQL_ASSERTION,
            import_violations(root, SQL_OWNER, JAVA_SQL),
        ),
        report_opaque_assertion(root),
        report_raw_connection_assertion(root),
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
