#!/usr/bin/env python3
"""Apply one compiling Authorization header mutant in place.

Every mutant here is a spelling of the header rule that some real HTTP stack
actually uses, which is why they compile and why they are worth defending
against: case-sensitive scheme comparison (what this backend did until #270),
accepting a bare credential with no scheme (also what it did), accepting any
scheme's parameter, and trimming the credential. Each is a plausible reading of
"parse the Authorization header", and each disagrees with FastAPI.
"""

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
        choices=(
            "case-sensitive-scheme",
            "accept-schemeless",
            "any-scheme-passes",
            "trim-credential",
        ),
    )
    parser.add_argument("project", type=Path)
    args = parser.parse_args()

    project = require_workdir(args.project)
    auth = project / "src/svc/auth.dawn"

    if args.mutant == "case-sensitive-scheme":
        # What the code did before #270: exact-match the scheme, so `bearer`
        # (which FastAPI accepts) gets a 401.
        replace_once(
            auth,
            "      if str.to_lower(str.slice(h, 0, i)) == want {\n",
            "      if str.slice(h, 0, i) == want {\n",
        )
    elif args.mutant == "accept-schemeless":
        # Also what it did before #270: no space means the whole header value is
        # the token, so `Authorization: <jwt>` authenticates.
        replace_once(
            auth,
            '  match str.index_of(h, " ") {\n    None -> ""\n',
            '  match str.index_of(h, " ") {\n    None -> h\n',
        )
    elif args.mutant == "any-scheme-passes":
        # Take the parameter whatever the scheme was, so `Basic <b64>` would
        # authenticate a Bearer route.
        replace_once(
            auth,
            '      } else {\n        ""\n      }\n  }\n',
            "      } else {\n        str.slice(h, i + 1, str.len(h))\n      }\n  }\n",
        )
    else:
        # Tidy up the credential, which accepts `Bearer  <jwt>` -- a header
        # FastAPI's partition(" ") leaves with a leading space, so it 401s there.
        replace_once(
            auth,
            "        str.slice(h, i + 1, str.len(h))\n      } else {\n",
            "        str.trim(str.slice(h, i + 1, str.len(h)))\n      } else {\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
