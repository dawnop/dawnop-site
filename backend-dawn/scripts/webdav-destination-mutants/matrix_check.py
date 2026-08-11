#!/usr/bin/env python3
"""Validate the versioned WebDAV Destination mutant ownership matrix."""

import argparse
from pathlib import Path

VERSION = "2"
VALID_ROLES = {"test", "qiniu"}
EXPECTED_ROWS = [
    "drop-dot-check|test|dest_rel rejects one-dot segments after one decode",
    "drop-dotdot-check|test|dest_rel rejects two-dot segments after one decode",
    "reject-dot-containing-names|test|dest_rel accepts names that merely contain dots",
    "drop-https-scheme|test|dest_rel accepts supported HTTP schemes",
    "allow-unsupported-scheme|test|dest_rel rejects unsupported schemes",
    "compare-host-case-sensitively|test|dest_rel compares authority hosts case-insensitively",
    "compare-ports-literally|test|dest_rel normalizes default HTTP ports",
    "use-http-default-for-https|test|destination default ports are scheme-correct",
    "allow-foreign-authority|test|dest_rel rejects a foreign authority",
    "uncatch-host-uri|test|dest_rel maps malformed Host syntax to 400",
    "accept-non-simple-reference|test|dest_rel rejects non-Simple-ref relative forms",
    "uncatch-destination-uri|test|dest_rel maps malformed URI syntax to 400",
    "unwrap-opaque-raw-path|test|dest_rel rejects opaque URI forms",
    "ignore-destination-query|test|dest_rel rejects query components",
    "ignore-destination-fragment|test|dest_rel rejects fragment components",
    "destination-prefix-fail-open|test|dest_rel confines paths to the DAV prefix",
    "skip-unreserved-prefix-normalization|test|dest_rel normalizes unreserved prefix octets",
    "restore-replacement-utf8|test|dest_rel rejects invalid percent UTF-8 without aliasing",
    "purge-before-parent-check|qiniu|copy.dest.file-parent-existing.preserved",
    "accept-file-parent|test|parent check accepts only root or a directory row",
]
EXPECTED_MUTANTS = [row.split("|", 1)[0] for row in EXPECTED_ROWS]


def validate(lines: list[str], listed_mutants: list[str]) -> list[tuple[str, str, str]]:
    content = [
        line.strip() for line in lines if line.strip() and not line.startswith("#")
    ]
    if not content or content[0] != f"version={VERSION}":
        raise ValueError(f"matrix version must be {VERSION}")
    if any(line.startswith("version=") for line in content[1:]):
        raise ValueError("matrix has duplicate version rows")

    rows = content[1:]
    parsed = []
    seen_mutants = set()
    seen_owners = set()
    for row in rows:
        if row.count("|") != 2:
            raise ValueError(f"malformed matrix row: {row}")
        mutant, role, owner = row.split("|", 2)
        if not mutant or not role or not owner:
            raise ValueError(f"incomplete matrix row: {row}")
        if role not in VALID_ROLES:
            raise ValueError(f"unknown matrix role: {role}")
        if mutant in seen_mutants:
            raise ValueError(f"duplicate matrix mutant: {mutant}")
        if owner in seen_owners:
            raise ValueError(f"duplicate matrix owner: {owner}")
        seen_mutants.add(mutant)
        seen_owners.add(owner)
        parsed.append((mutant, role, owner))

    if rows != EXPECTED_ROWS:
        raise ValueError("matrix order or membership differs from the hand-owned set")
    if listed_mutants != EXPECTED_MUTANTS:
        raise ValueError("mutate.py --list differs from matrix membership")
    return parsed


def expect_rejected(
    name: str, lines: list[str], mutants: list[str], message: str
) -> None:
    try:
        validate(lines, mutants)
    except ValueError as error:
        if message not in str(error):
            raise AssertionError(f"{name}: wrong rejection: {error}") from error
        print(f"PASS  matrix negative control {name}: {error}")
        return
    raise AssertionError(f"{name}: invalid matrix was accepted")


def self_test() -> None:
    base = [f"version={VERSION}", *EXPECTED_ROWS]
    validate(base, EXPECTED_MUTANTS)
    expect_rejected(
        "duplicate-mutant",
        [*base, f"{EXPECTED_MUTANTS[0]}|test|another owner"],
        EXPECTED_MUTANTS,
        "duplicate matrix mutant",
    )
    expect_rejected(
        "duplicate-owner",
        [*base, f"unknown-mutant|test|{EXPECTED_ROWS[0].split('|', 2)[2]}"],
        EXPECTED_MUTANTS,
        "duplicate matrix owner",
    )
    expect_rejected(
        "unknown-role",
        [*base[:-1], "accept-file-parent|unknown|new owner"],
        EXPECTED_MUTANTS,
        "unknown matrix role",
    )
    expect_rejected(
        "role-drift",
        [
            *base[:-2],
            EXPECTED_ROWS[-2].replace("|qiniu|", "|test|"),
            EXPECTED_ROWS[-1],
        ],
        EXPECTED_MUTANTS,
        "hand-owned set",
    )
    expect_rejected("omitted-row", base[:-1], EXPECTED_MUTANTS, "hand-owned set")
    expect_rejected(
        "unknown-row",
        [*base[:-1], "unknown-mutant|test|unknown owner"],
        EXPECTED_MUTANTS,
        "hand-owned set",
    )
    expect_rejected(
        "wrong-version", ["version=1", *EXPECTED_ROWS], EXPECTED_MUTANTS, "version"
    )
    expect_rejected("mutator-omission", base, EXPECTED_MUTANTS[:-1], "mutate.py --list")
    expect_rejected(
        "mutator-unknown",
        base,
        [*EXPECTED_MUTANTS, "unknown-mutant"],
        "mutate.py --list",
    )
    expect_rejected(
        "mutator-duplicate",
        base,
        [*EXPECTED_MUTANTS, EXPECTED_MUTANTS[-1]],
        "mutate.py --list",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path)
    parser.add_argument("--mutants", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
    if args.matrix is not None or args.mutants is not None:
        if args.matrix is None or args.mutants is None:
            parser.error("--matrix and --mutants must be provided together")
        parsed = validate(
            args.matrix.read_text(encoding="utf-8").splitlines(),
            args.mutants.read_text(encoding="utf-8").splitlines(),
        )
        print(
            f"PASS  Destination matrix v{VERSION}: {len(parsed)} owned mutants across exact roles"
        )
    if not args.self_test and args.matrix is None:
        parser.error("choose --self-test or provide --matrix and --mutants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
