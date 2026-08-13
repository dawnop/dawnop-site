#!/usr/bin/env python3
"""Validate the versioned FM ancestor mutant ownership matrix."""

import argparse
from pathlib import Path

VERSION = "15"
VALID_ROLES = {"test", "contract", "qiniu"}
EXPECTED_ROWS = [
    "immediate-tx-as-deferred|test|with_immediate_tx reserves the writer before its body",
    "immediate-tx-panic-no-rollback|test|with_immediate_tx rolls back when its body panics",
    "repo-file-ancestor-fail-open|test|insert_folder_strict rejects an existing file ancestor",
    "repo-immediate-parent-only|test|insert_file_strict checks ancestors above an existing parent row",
    "repo-directory-target-fail-open|test|insert_file rejects an existing directory target",
    "repo-file-target-descendants-preflight-fail-open|contract|file-target.descendants.preflight",
    "repo-file-target-descendants-final-fail-open|contract|file-target.descendants.final-race",
    "repo-fm-insert-split-transaction|test|FM inserts roll back auto-created ancestors when the final write fails",
    "repo-webdav-strict-insert-uses-fm|test|WebDAV strict inserts reject missing ancestors without backfill",
    "repo-rename-fill-missing|contract|fm.rename.missing-ancestor-no-fill",
    "repo-fm-reparent-split-transaction|test|FM fill reparent rolls back backfilled ancestors when path rewrite fails",
    "repo-fm-move-request-split-transaction|contract|fm.move.request-atomic",
    "repo-webdav-strict-reparent-uses-fm|test|WebDAV strict reparent rejects missing and file ancestors",
    "repo-file-upsert-disappeared-ok|test|insert_file fails closed when an ignored upsert removes its target",
    "repo-empty-subtree-ok|test|subtree mutations reject an empty source",
    "repo-copy-skip-source-revalidation|test|copy commits reject a vanished source snapshot",
    "repo-copy-skip-target-revalidation|test|copy commits revalidate every mapped target",
    "repo-copy-source-shape-preflight-fail-open|contract|copy.source-object-shape.preflight",
    "repo-copy-blank-key-preflight-fail-open|contract|copy.blank-source-key.preflight",
    "repo-copy-source-shape-final-fail-open|test|copy commits reject malformed source object rows",
    "repo-destination-subtree-preflight-fail-open|contract|copy-move.destination-subtree.preflight",
    "repo-destination-subtree-final-fail-open|test|copy, move, and rename commits reject unmapped destination descendants",
    "repo-literal-prefix-use-like|contract|tree.literal-prefix.isolated",
    "repo-subtree-child-first|contract|webdav.copy.parent-before-child",
    "repo-fm-copy-split-transaction|contract|fm.copy.metadata-atomic",
    "repo-webdav-copy-split-transaction|contract|webdav.copy.metadata-atomic",
    "fm-reject-missing-ancestor|contract|fm.missing-ancestors.auto-created",
    "fm-preflight-after-qiniu|contract|fm.external-effects.preflight",
    "fm-upload-reuse-existing-key|contract|fm.upload.final-write-isolated",
    "fm-copy-root-only-preflight|contract|fm.copy.deep-target-preflight",
    "fm-copy-plain-error-as-502|contract|fm.copy.error-classification",
    "fm-skip-directory-target-preflight|contract|fm.directory-target.preflight",
    "fm-skip-upload-token-preflight|contract|fm.upload-token.preflight",
    "fm-register-stat-before-preflight|contract|fm.register.preflight",
    "fm-skip-reparent-validation|contract|fm.move.reparent-preflight",
    "fm-skip-rename-validation|contract|fm.rename.file-ancestor-preflight",
    "fm-map-conflict-as-default|contract|fm.conflict.maps-409",
    "repo-subtree-shape-fail-open|contract|subtree.internal-shape.preflight",
    "webdav-skip-full-ancestor-validation|contract|webdav.full-ancestor.preflight",
    "webdav-missing-parent-fail-open|contract|webdav.missing-parent.rejects",
    "webdav-reverse-overlap-fail-open|contract|webdav.reverse-overlap.rejects",
    "webdav-copy-root-only-preflight|test|WebDAV copy preflight rejects every mapped target",
    "object-gc-panic-leak|test|best-effort GC isolates success, errors, and panics",
    "object-gc-release-lock-before-delete|contract|webdav.overwrite.gc-after-switch",
    "webdav-put-use-preflight-superseded|contract|webdav.put.actual-superseded-key",
    "gc-skip-live-key-reference|contract|object-gc.live-key-reference",
    "webdav-delete-qiniu-before-detach|contract|webdav.delete.metadata-first.concurrent-rename",
    "webdav-delete-subtree-rowwise|contract|webdav.delete.metadata-subtree-atomic",
    "webdav-copy-overwrite-delete-on-failure|contract|webdav.overwrite.copy-failure-preserves-target",
    "webdav-move-overwrite-delete-on-failure|contract|webdav.overwrite.move-failure-preserves-target",
    "webdav-collection-overwrite-delete-on-failure|contract|webdav.overwrite.metadata-switch-atomic",
    "repo-webdav-copy-overwrite-prepare-root-blind|test|WebDAV COPY prepare rejects orphan descendants even with overwrite",
    "repo-webdav-delete-root-blind|test|WebDAV delete preserves descendants when the requested root is absent",
    "repo-fm-delete-list-root-blind|test|FM delete skips an absent root without deleting its descendants",
    "webdav-precondition-map-as-conflict|test|WebDAV precondition errors map to 412 at preflight and final edges",
    "fm-save-codepoint-size|qiniu|fm.save.utf8-byte-size",
    "webdav-propfind-depth-singleton-fail-open|test|PROPFIND rejects unsupported singleton Depth values",
    "webdav-propfind-depth-repeat-fail-open|test|PROPFIND rejects repeated Depth headers",
    "qiniu-upload-mime-passthrough|test|Qiniu upload multipart replaces unsafe persisted MIME wholesale",
    "multipart-part-ctype-literal-spelling|test|a declared part Content-Type survives every legal header spelling",
    "fm-persisted-mime-passthrough|qiniu|fm.persisted-mime.fail-safe",
    "fm-split-trims-addressing|contract|fm.addressing.lossless",
]
EXPECTED_MUTANTS = [row.split("|", 1)[0] for row in EXPECTED_ROWS]
EXPECTED_CONTRACT_OWNERS = [
    row.split("|", 2)[2] for row in EXPECTED_ROWS if row.split("|", 2)[1] == "contract"
]
EXPECTED_QINIU_OWNERS = [
    row.split("|", 2)[2] for row in EXPECTED_ROWS if row.split("|", 2)[1] == "qiniu"
]
EXPECTED_ROLE_COUNTS = {"test": 25, "contract": 35, "qiniu": 2}


def validate(lines, listed_mutants, listed_assertions, listed_qiniu_assertions):
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
    role_counts = {
        role: sum(1 for _, actual_role, _ in parsed if actual_role == role)
        for role in VALID_ROLES
    }
    if role_counts != EXPECTED_ROLE_COUNTS:
        raise ValueError(f"matrix role counts differ: {role_counts}")
    if listed_mutants != EXPECTED_MUTANTS:
        raise ValueError("mutate.py --list differs from matrix membership")
    if len(listed_assertions) != len(set(listed_assertions)):
        raise ValueError("run.py --list contains duplicate contract owners")
    expected_assertions = set(EXPECTED_CONTRACT_OWNERS)
    actual_assertions = set(listed_assertions)
    missing_assertions = sorted(expected_assertions - actual_assertions)
    extra_assertions = sorted(actual_assertions - expected_assertions)
    if missing_assertions:
        raise ValueError(
            f"run.py --list is missing matrix contract owners: {missing_assertions}"
        )
    if extra_assertions:
        raise ValueError(f"run.py --list has extra contract owners: {extra_assertions}")
    if len(listed_qiniu_assertions) != len(set(listed_qiniu_assertions)):
        raise ValueError("contract_qiniu.py contains duplicate case owners")
    missing_qiniu_assertions = sorted(
        set(EXPECTED_QINIU_OWNERS) - set(listed_qiniu_assertions)
    )
    if missing_qiniu_assertions:
        raise ValueError(
            "contract_qiniu.py is missing matrix qiniu owners: "
            f"{missing_qiniu_assertions}"
        )
    return parsed


def expect_rejected(
    name, lines, mutants, message, assertions=None, qiniu_assertions=None
):
    try:
        validate(
            lines,
            mutants,
            EXPECTED_CONTRACT_OWNERS if assertions is None else assertions,
            EXPECTED_QINIU_OWNERS if qiniu_assertions is None else qiniu_assertions,
        )
    except ValueError as error:
        if message not in str(error):
            raise AssertionError(f"{name}: wrong rejection: {error}") from error
        print(f"PASS  matrix negative control {name}: {error}")
        return
    raise AssertionError(f"{name}: invalid matrix was accepted")


def self_test():
    base = [f"version={VERSION}", *EXPECTED_ROWS]
    validate(
        base,
        EXPECTED_MUTANTS,
        EXPECTED_CONTRACT_OWNERS,
        EXPECTED_QINIU_OWNERS,
    )
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
    # the drifted role is derived, not spelled: writing one in by hand made this
    # control a no-op the moment a row of another role was appended, and a
    # no-op rewrite is accepted by validate() — the self-test then "passed" by
    # checking nothing
    last_mutant, last_role, last_owner = EXPECTED_ROWS[-1].split("|", 2)
    other_role = sorted(VALID_ROLES - {last_role})[0]
    expect_rejected(
        "role-drift",
        [*base[:-1], f"{last_mutant}|{other_role}|{last_owner}"],
        EXPECTED_MUTANTS,
        "hand-owned set",
    )
    expect_rejected(
        "owner-drift",
        [*base[:-1], EXPECTED_ROWS[-1] + " changed"],
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
    expect_rejected(
        "assertion-missing",
        base,
        EXPECTED_MUTANTS,
        "missing matrix contract owners",
        EXPECTED_CONTRACT_OWNERS[:-1],
    )
    expect_rejected(
        "assertion-extra",
        base,
        EXPECTED_MUTANTS,
        "extra contract owners",
        [*EXPECTED_CONTRACT_OWNERS, "unknown.contract.owner"],
    )
    expect_rejected(
        "assertion-duplicate",
        base,
        EXPECTED_MUTANTS,
        "duplicate contract owners",
        [*EXPECTED_CONTRACT_OWNERS, EXPECTED_CONTRACT_OWNERS[-1]],
    )
    expect_rejected(
        "qiniu-assertion-missing",
        base,
        EXPECTED_MUTANTS,
        "missing matrix qiniu owners",
        qiniu_assertions=[],
    )
    expect_rejected(
        "qiniu-assertion-duplicate",
        base,
        EXPECTED_MUTANTS,
        "duplicate case owners",
        qiniu_assertions=[*EXPECTED_QINIU_OWNERS, EXPECTED_QINIU_OWNERS[-1]],
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path)
    parser.add_argument("--mutants", type=Path)
    parser.add_argument("--assertions", type=Path)
    parser.add_argument("--qiniu-assertions", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    matrix_inputs = (
        args.matrix,
        args.mutants,
        args.assertions,
        args.qiniu_assertions,
    )
    if any(value is not None for value in matrix_inputs):
        if any(value is None for value in matrix_inputs):
            parser.error(
                "--matrix, --mutants, --assertions and --qiniu-assertions "
                "must be provided together"
            )
        parsed = validate(
            args.matrix.read_text(encoding="utf-8").splitlines(),
            args.mutants.read_text(encoding="utf-8").splitlines(),
            args.assertions.read_text(encoding="utf-8").splitlines(),
            args.qiniu_assertions.read_text(encoding="utf-8").splitlines(),
        )
        print(
            f"PASS  FM ancestor matrix v{VERSION}: {len(parsed)} owned mutants "
            f"({EXPECTED_ROLE_COUNTS['test']} test, "
            f"{EXPECTED_ROLE_COUNTS['contract']} contract, "
            f"{EXPECTED_ROLE_COUNTS['qiniu']} qiniu)"
        )
    if not args.self_test and args.matrix is None:
        parser.error("choose --self-test or provide --matrix and --mutants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
