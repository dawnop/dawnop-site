#!/usr/bin/env python3
"""Apply one compiling database connection boundary mutant in place."""

import argparse
from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def append_leak(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    marker = "pub fn leaked_java_sql(c: Connection) -> Connection = c"
    if marker in text:
        raise ValueError(f"leak already present in {path}")
    path.write_text(f"{text.rstrip()}\n\n{marker}\n", encoding="utf-8")


def insert_raw_leak(path: Path) -> None:
    replace_once(
        path,
        "fn raw(c: DbConn) -> Connection = c\n",
        "fn raw(c: DbConn) -> Connection = c\n\n"
        "pub fn leaked_raw(c: DbConn) -> Connection = raw(c)\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mutant",
        choices=(
            "leak-java-sql",
            "leak-raw-connection",
            "make-dbconn-transparent",
        ),
    )
    parser.add_argument("project", type=Path)
    args = parser.parse_args()

    sql = args.project / "src/db/sql.dawn"
    if args.mutant == "make-dbconn-transparent":
        replace_once(
            sql,
            "pub opaque type DbConn = Connection\n",
            "pub alias DbConn = Connection\n",
        )
    elif args.mutant == "leak-java-sql":
        repo = args.project / "src/repo/repo_tag.dawn"
        replace_once(
            repo,
            "use db/sql.{DbConn, ",
            'use java "java.sql.Connection"\nuse db/sql.{DbConn, ',
        )
        append_leak(repo)
    else:
        insert_raw_leak(sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
