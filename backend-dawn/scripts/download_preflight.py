"""Check an explicit deployment cohort manifest without contacting a service."""

import argparse
import json
import re
import sys
from pathlib import Path

WRITERS = {
    "fm-register",
    "fm-create",
    "fm-save",
    "fm-upload",
    "fm-copy",
    "dav-put",
    "dav-copy",
    "drop-register",
    "drop-put",
}
METHODS = {"GET", "HEAD", "PROPFIND"}


def check(manifest):
    candidate = manifest.get("candidate", "")
    methods, writers = manifest.get("methods", {}), manifest.get("writers", {})
    row = manifest.get("metadata_summary", {})
    config = (
        bool(re.fullmatch(r"[0-9a-f]{40}", candidate))
        and manifest.get("capability") == "on"
        and set(methods) == METHODS
        and set(writers) == WRITERS
        and all(value == candidate for value in [*methods.values(), *writers.values()])
    )
    metadata = (
        row.get("version") == "download-k2-backfill-v1"
        and row.get("ready") is True
        and bool(re.fullmatch(r"identity-v1:[0-9a-f]{64}", manifest.get("profile", "")))
        and row.get("profile") == manifest.get("profile")
        and bool(re.fullmatch(r"[0-9a-f]{64}", manifest.get("schema_fingerprint", "")))
        and row.get("schema_fingerprint") == manifest.get("schema_fingerprint")
        and all(
            type(row.get(name)) is int and row[name] >= 0
            for name in ("rows", "files", "directories", "complete")
        )
        and row.get("rows") == row.get("files", -1) + row.get("directories", -1)
        and row.get("complete") == row.get("files")
        and all(
            row.get(name, -1) == 0
            for name in (
                "unresolved",
                "unscanned",
                "missing_key",
                "negative_size",
                "duplicate_keys",
                "conflicts",
                "metadata_failures",
                "database_failures",
                "object_missing",
                "budget_exhausted",
            )
        )
    )
    ready = config and metadata
    gates = {
        "cohort": "LOCAL_PASS" if ready else "FAIL",
        "in_server_replica": "PRODUCTION_PENDING",
        "head_get_equivalence": "PRODUCTION_PENDING",
        "real_clients": "PRODUCTION_PENDING",
        "production_resources": "PRODUCTION_PENDING",
        "old_tokens_invalidated": "PRODUCTION_PENDING",
        "authorized_s13_cutover": "PRODUCTION_PENDING",
    }
    blockers = [
        name for name, value in gates.items() if value not in {"PASS", "LOCAL_PASS"}
    ]
    return {
        "group_ready": ready,
        "method_count": len(methods),
        "writer_count": len(writers),
        "gate_outcomes": gates,
        "production_blockers": blockers,
        "production_gate": "BLOCKED" if blockers else "PASS",
    }


def self_test():
    sha = "a" * 40
    metadata = {
        "version": "download-k2-backfill-v1",
        "ready": True,
        "rows": 3,
        "files": 2,
        "directories": 1,
        "complete": 2,
        "profile": "identity-v1:" + "c" * 64,
        "schema_fingerprint": "d" * 64,
    }
    metadata.update(
        {
            name: 0
            for name in (
                "unresolved",
                "unscanned",
                "missing_key",
                "negative_size",
                "duplicate_keys",
                "conflicts",
                "metadata_failures",
                "database_failures",
                "object_missing",
                "budget_exhausted",
            )
        }
    )
    manifest = {
        "candidate": sha,
        "capability": "on",
        "methods": dict.fromkeys(METHODS, sha),
        "writers": dict.fromkeys(WRITERS, sha),
        "metadata_summary": metadata,
        "profile": metadata["profile"],
        "schema_fingerprint": metadata["schema_fingerprint"],
    }
    assert check(manifest)["group_ready"]
    for key in METHODS:
        changed = {**manifest, "methods": {**manifest["methods"], key: "b" * 40}}
        assert not check(changed)["group_ready"]
    assert not check({**manifest, "capability": "off"})["group_ready"]
    assert not check({**manifest, "writers": {}})["group_ready"]
    assert not check({**manifest, "metadata_summary": {**metadata, "unresolved": 1}})[
        "group_ready"
    ]
    assert not check({**manifest, "profile": "identity-v1:" + "e" * 64})["group_ready"]
    assert not check({**manifest, "schema_fingerprint": "e" * 64})["group_ready"]
    print(json.dumps({"preflight_tests": 9, "passed": 9}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        result = check(json.loads(args.manifest.read_text()))
    except Exception:
        result = {**check({}), "failures": 1}
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("group_ready") else 1


if __name__ == "__main__":
    sys.exit(main())
