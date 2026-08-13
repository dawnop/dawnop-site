#!/usr/bin/env python3
"""Apply one compiling outbound-HTTP deadline mutant in place."""

import argparse
from pathlib import Path

MUTANTS = (
    "collapse-transfer-tier",
    "drop-deadline",
    "inflate-deadline",
    "mute-timeout-text",
    "transfer-tier-on-management",
)


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mutant", choices=MUTANTS)
    parser.add_argument("project", type=Path)
    args = parser.parse_args()

    http = args.project / "src/util/http.dawn"

    if args.mutant == "drop-deadline":
        # No deadline at all: the state this task found the code in.
        replace_once(
            http,
            "  let timed = base.timeout(Duration.ofSeconds(budget_s)!)!\n",
            "  let timed = base\n",
        )
    elif args.mutant == "inflate-deadline":
        # A deadline that is present but useless. `.timeout(` still appears in
        # the source, so any assertion that greps for the call survives this;
        # only actually timing a stalled peer catches it.
        replace_once(
            http,
            "  let timed = base.timeout(Duration.ofSeconds(budget_s)!)!\n",
            "  let timed = base.timeout(Duration.ofSeconds(86400)!)!\n",
        )
    elif args.mutant == "transfer-tier-on-management":
        # Deadline present and sane, but the small management calls inherit the
        # transfer ceiling, so gc's delete_obj could hold the SQLite write lock
        # for an hour instead of ten seconds.
        replace_once(
            http,
            "  send_body(method, url, headers, publisher, MANAGEMENT_TIMEOUT_S)\n",
            "  send_body(method, url, headers, publisher, TRANSFER_TIMEOUT_S)\n",
        )
    elif args.mutant == "collapse-transfer-tier":
        # The two tiers stop being two tiers, capping every upload and download
        # at the management ceiling.
        replace_once(
            http,
            "const TRANSFER_TIMEOUT_S: Int = 3600\n",
            "const TRANSFER_TIMEOUT_S: Int = 10\n",
        )
    else:
        # A timeout stops announcing itself and reads as a generic fault again.
        replace_once(
            http,
            '    "outbound HTTP timed out after ${to_string(budget_s)}s: ${fe_text(e)}"\n',
            "    fe_text(e)\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
