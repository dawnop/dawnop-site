#!/usr/bin/env python3
"""Apply one compiling src/db/sql.dawn boundary mutant in place.

Two boundaries live in that file and both are here: the FFI one (java.sql stays
inside the module, DbConn is opaque) and the transaction one (what happens when
a connection already has a transaction open). The second was added with #254,
whose finding was that the entry guard could not see the transactions this
backend actually opens -- so the mutants for it are checked by running the
suite, not by the static FFI walker.
"""

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
            "guard-tx-by-autocommit",
            "leak-java-sql",
            "leak-raw-connection",
            "make-dbconn-transparent",
            "rollback-open-tx-on-entry",
            "unnamed-nested-refusal",
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
    elif args.mutant == "guard-tx-by-autocommit":
        # The defect #254 was about, restored: the probe answers from
        # sqlite-jdbc's JDBC-level flag instead of asking SQLite. The flag never
        # sees a raw `begin immediate`, so it reports "no transaction" while one
        # is open -- and with_tx walks into setAutoCommit(false) and dies there.
        replace_once(
            sql,
            "fn in_tx(c: DbConn) -> Bool !io =\n"
            '  match exec(c, "begin", []) {\n'
            "    Ok(_) -> {\n"
            '      let _r = exec(c, "rollback", [])\n'
            "      false\n"
            "    }\n"
            "    Err(m) -> nested_tx_refusal(m)\n"
            "  }\n",
            "fn in_tx(c: DbConn) -> Bool !io = not raw(c).getAutoCommit()\n",
        )
    elif args.mutant == "rollback-open-tx-on-entry":
        # The fix nobody should make: the entry now sees the open transaction
        # and "recovers" by rolling it back and starting its own. That discards
        # uncommitted writes belonging to a caller further out, quietly.
        replace_once(
            sql,
            "      if nested_tx_refusal(m) {\n"
            '        Err("with_immediate_tx entered with a transaction already open on this connection")\n'
            "      } else {\n",
            "      if nested_tx_refusal(m) {\n"
            '        let _r = exec(c, "rollback", [])\n'
            '        let _b = exec(c, "begin immediate", [])?\n'
            "        bracket(c, h => rollback_immediate(h), _h => finish_immediate_tx(c, body))\n"
            "      } else {\n",
        )
    elif args.mutant == "unnamed-nested-refusal":
        # The refusal stops naming itself and reaches the operator as SQLite's
        # own "cannot start a transaction within a transaction", which reads
        # like a driver fault rather than a caller that nested.
        replace_once(
            sql,
            '        Err("with_immediate_tx entered with a transaction already open on this connection")\n',
            "        Err(m)\n",
        )
    else:
        insert_raw_leak(sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
