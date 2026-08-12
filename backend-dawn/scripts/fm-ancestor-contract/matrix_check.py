#!/usr/bin/env python3
"""Validate the versioned FM ancestor mutant ownership matrix."""

import argparse
from pathlib import Path

VERSION = "3"
VALID_ROLES = {"test", "contract"}
EXPECTED_ROWS = [
    "immediate-tx-as-deferred|test|with_immediate_tx reserves the writer before its body",
    "repo-file-ancestor-fail-open|test|insert_folder_strict rejects an existing file ancestor",
    "repo-immediate-parent-only|test|insert_file_strict checks ancestors above an existing parent row",
    "repo-directory-target-fail-open|test|insert_file rejects an existing directory target",
    "repo-fm-insert-split-transaction|test|FM inserts roll back auto-created ancestors when the final write fails",
    "repo-webdav-strict-insert-uses-fm|test|WebDAV strict inserts reject missing ancestors without backfill",
    "repo-rename-fill-missing|contract|fm.rename.missing-ancestor-no-fill",
    "repo-fm-reparent-split-transaction|test|FM fill reparent rolls back backfilled ancestors when path rewrite fails",
    "repo-webdav-strict-reparent-uses-fm|test|WebDAV strict reparent rejects missing and file ancestors",
    "repo-file-upsert-disappeared-ok|test|insert_file fails closed when an ignored upsert removes its target",
    "repo-literal-prefix-use-like|contract|tree.literal-prefix.isolated",
    "repo-subtree-child-first|contract|webdav.copy.parent-before-child",
    "fm-reject-missing-ancestor|contract|fm.missing-ancestors.auto-created",
    "fm-preflight-after-qiniu|contract|fm.external-effects.preflight",
    "fm-skip-directory-target-preflight|contract|fm.directory-target.preflight",
    "fm-skip-upload-token-preflight|contract|fm.upload-token.preflight",
    "fm-register-gc-before-preflight|contract|fm.register.preflight",
    "fm-skip-reparent-validation|contract|fm.move.reparent-preflight",
    "fm-skip-rename-validation|contract|fm.rename.file-ancestor-preflight",
    "fm-map-conflict-as-default|contract|fm.conflict.maps-409",
    "repo-subtree-shape-fail-open|contract|subtree.internal-shape.preflight",
    "webdav-skip-full-ancestor-validation|contract|webdav.full-ancestor.preflight",
    "webdav-missing-parent-fail-open|contract|webdav.missing-parent.rejects",
    "webdav-reverse-overlap-fail-open|contract|webdav.reverse-overlap.rejects",
]
EXPECTED_MUTANTS = [row.split("|", 1)[0] for row in EXPECTED_ROWS]


def validate(lines, listed_mutants):
    content = [
        line.strip() for line in lines if line.strip() and not line.startswith("#")
    ]
    if not content or content[0] != f"version={VERSION}":
        raise ValueError(f"matrix version must be {VERSION}")
    if any(line.startswith("version=") for line in content[1:]):
        raise ValueError("matrix has duplicate version rows")

    rows = content[1:]
    seen_mutants = set()
    seen_owners = set()
    parsed = []
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


def expect_rejected(name, lines, mutants, message):
    try:
        validate(lines, mutants)
    except ValueError as error:
        if message not in str(error):
            raise AssertionError(f"{name}: wrong rejection: {error}") from error
        print(f"PASS  matrix negative control {name}: {error}")
        return
    raise AssertionError(f"{name}: invalid matrix was accepted")


def self_test():
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
        [*base[:-1], "webdav-reverse-overlap-fail-open|unknown|new owner"],
        EXPECTED_MUTANTS,
        "unknown matrix role",
    )
    expect_rejected(
        "role-drift",
        [*base[:-1], EXPECTED_ROWS[-1].replace("|contract|", "|test|")],
        EXPECTED_MUTANTS,
        "hand-owned set",
    )
    expect_rejected(
        "owner-drift",
        [*base[:-1], EXPECTED_ROWS[-1].replace("rejects", "changed")],
        EXPECTED_MUTANTS,
        "hand-owned set",
    )
    expect_rejected("omitted-row", base[:-1], EXPECTED_MUTANTS, "hand-owned set")
    expect_rejected(
        "unknown-row",
        [*base[:-1], "unknown-mutant|contract|unknown owner"],
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


def main():
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
            f"PASS  FM ancestor matrix v{VERSION}: {len(parsed)} owned mutants across exact roles"
        )
    if not args.self_test and args.matrix is None:
        parser.error("choose --self-test or provide --matrix and --mutants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
