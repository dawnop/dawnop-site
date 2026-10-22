"""Prove that omitting the production JDBC handler fails effect checking."""

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dawn", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dawnop-db-handler-") as temporary:
        backend = Path(temporary)
        shutil.copytree(BACKEND / "app", backend / "app")
        shutil.copytree(BACKEND / "core", backend / "core")
        source = backend / "app/jvm/src/db/jdbc.dawn"
        text = source.read_text()
        start = text.index("  with handle Db {")
        end = text.index("\n  body()", start)
        source.write_text(text[:start] + text[end:])
        result = subprocess.run(
            [args.dawn, "check", backend / "app/jvm"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        expected = "`with_jdbc` uses the effect `Db`"
        if result.returncode == 0 or expected not in result.stdout:
            print(result.stdout)
            print("FAIL  db.handler.required: missing handler was not rejected")
            return 1
        print(result.stdout, end="")
        print("PASS  db.handler.required: removing the real handler fails checking")
        # The handler discharges Db only; a polymorphic callback retains its
        # other capabilities at the outer call site.
        source.write_text(
            text
            + "\npub effect Residual {\n  fn probe() -> Int\n}\n"
            + "fn missing_residual() -> Int !io = "
            + 'with_connection(open("jdbc:sqlite::memory:"), () => probe())\n'
        )
        result = subprocess.run(
            [args.dawn, "check", backend / "app/jvm"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        expected = "`missing_residual` uses the effect `Residual`"
        if result.returncode == 0 or expected not in result.stdout:
            print(result.stdout)
            print("FAIL  db.handler.residual: callback capability was erased")
            return 1
        print(
            "PASS  db.handler.residual: unrelated callback capability remains required"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
