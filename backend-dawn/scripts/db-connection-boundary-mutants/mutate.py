#!/usr/bin/env python3
"""Apply one compiling SQL and JDBC boundary mutant in place.

The portable SQL module and JVM adapter share these boundaries: the FFI one (java.sql stays
inside the module, DbConn is opaque) and the transaction one (what happens when
a connection already has a transaction open). The second was added with #254,
whose finding was that the entry guard could not see the transactions this
backend actually opens -- so the mutants for it are checked by running the
suite, not by the static FFI walker.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mutant_workdir import require_workdir  # noqa: E402
from test_project import source_path  # noqa: E402


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
            "guard-tx-by-autocommit",
            "leak-java-sql",
            "leak-raw-connection",
            "make-dbconn-transparent",
            "rollback-open-tx-on-entry",
            "unnamed-nested-refusal",
            "first-duplicate-column-wins",
        ),
    )
    parser.add_argument("project", type=Path)
    args = parser.parse_args()

    project = require_workdir(args.project)

    sql = source_path(project, "src/db/jdbc.dawn")
    core_sql = source_path(project, "src/db/sql.dawn")
    if args.mutant == "make-dbconn-transparent":
        replace_once(
            sql,
            "pub opaque type DbConn = Connection\n",
            "pub alias DbConn = Connection\n",
        )
    elif args.mutant == "leak-java-sql":
        repo = source_path(project, "src/repo/repo_tag.dawn")
        replace_once(
            repo,
            "use db/sql.{Db, ",
            'use java "java.sql.Connection"\nuse db/sql.{Db, ',
        )
        append_leak(repo)
    elif args.mutant == "guard-tx-by-autocommit":
        # The defect #254 was about, restored: the probe answers from
        # sqlite-jdbc's JDBC-level flag instead of asking SQLite. The flag never
        # sees a raw `begin immediate`, so it reports "no transaction" while one
        # is open. Both the direct probe and the named refusal must catch it.
        text = sql.read_text()
        start = text.index("fn jdbc_in_transaction(")
        end = text.index("\nfn jdbc_last_id", start)
        sql.write_text(
            text[:start]
            + "fn jdbc_in_transaction(c: DbConn) -> Result[Bool, String] !io =\n"
            "  Ok(not raw(c).getAutoCommit())\n" + text[end:]
        )
    elif args.mutant == "rollback-open-tx-on-entry":
        # The fix nobody should make: the entry now sees the open transaction
        # and "recovers" by rolling it back and starting its own. That discards
        # uncommitted writes belonging to a caller further out, quietly.
        replace_once(
            core_sql,
            "      if nested_tx_refusal(m) {\n"
            '        Err("with_immediate_tx entered with a transaction already open on this connection")\n'
            "      } else {\n",
            "      if nested_tx_refusal(m) {\n"
            '        let _r = execute("rollback", [])\n'
            '        let _b = execute("begin immediate", [])?\n'
            "        bracket((), _h => rollback_immediate(), _h => finish_immediate_tx(body))\n"
            "      } else {\n",
        )
    elif args.mutant == "unnamed-nested-refusal":
        # The refusal stops naming itself and reaches the operator as SQLite's
        # own "cannot start a transaction within a transaction", which reads
        # like a driver fault rather than a caller that nested.
        replace_once(
            core_sql,
            '        Err("with_immediate_tx entered with a transaction already open on this connection")\n',
            "        Err(m)\n",
        )
    elif args.mutant == "first-duplicate-column-wins":
        replace_once(
            core_sql,
            "  map.from(map(range(0, len(names)), i => (names[i], i)))\n",
            "  map.from(map(range(0, len(names)), i => (names[len(names) - 1 - i], len(names) - 1 - i)))\n",
        )
    else:
        insert_raw_leak(sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
