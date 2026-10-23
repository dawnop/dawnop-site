#!/usr/bin/env python3
"""备份负控：只修改有哨兵的隔离副本，先证明指定判词红，再跑未变异正控。"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend-dawn/scripts/backup-engine"))
import runtime  # noqa: E402

GUARD = runtime.module(
    "backup_mutant_guard", REPO / "backend-dawn/scripts/mutant_workdir.py"
)
SHELL = "deploy/backup-db.sh"
CORE = "backend-dawn/core/src/backup.dawn"
JVM = "backend-dawn/app/jvm/src/db/db.dawn"
SNAPSHOT = '''DAWNOP_ENV=/dev/null DAWN_DB_PATH="$DB" "${engine[@]}" --backup "$tmp" \\
  || die "取快照或校验失败：$DB"'''
INTEGRITY_RESULT = '  if rows != [[VStr("ok")]] { return Err("backup integrity_check failed: ${to_string(rows)}") }'
CANDIDATES = r"""for f in "$DEST"/dawnop-*.db.gz; do
  base=${f##*/}
  [[ $base =~ ^dawnop-[0-9]{8}T[0-9]{6}Z\.db\.gz$ ]] || continue
  existing+=("$base")
done"""
CANDIDATES_WIDE = """for f in "$DEST"/*; do
  base=${f##*/}
  existing+=("$base")
done"""


def shell(old, new):
    return [(SHELL, old, new)]


# 名称、真实源码变形、必须转红的判词；只运行这些 owner，不能拿编译错误充杀伤。
MUTANTS = [
    (
        "rotate-keeps-everything",
        shell("if ((${#existing[@]} > KEEP)); then", "if false; then"),
        [
            "test_rotation_keeps_exactly_the_newest_keep",
            "test_rotation_does_not_touch_foreign_files",
        ],
    ),
    (
        "rotate-glob-too-wide",
        shell(CANDIDATES, CANDIDATES_WIDE),
        [
            "test_rotation_keeps_exactly_the_newest_keep",
            "test_rotation_does_not_touch_foreign_files",
        ],
    ),
    (
        "rotate-keep-off-by-one",
        shell(
            '"${sorted[@]:0:${#sorted[@]} - KEEP}"',
            '"${sorted[@]:0:${#sorted[@]} - KEEP - 1}"',
        ),
        [
            "test_rotation_keeps_exactly_the_newest_keep",
            "test_rotation_does_not_touch_foreign_files",
        ],
    ),
    (
        "skip-integrity-check",
        [
            (
                CORE,
                "  let checked = verify_copy()",
                "  let checked: Result[Unit, String] = Ok(())",
            )
        ],
        ["test_snapshot_that_reports_corruption_is_not_kept"],
    ),
    (
        "integrity-result-not-checked",
        [(CORE, INTEGRITY_RESULT, "  let _ = rows")],
        ["test_snapshot_that_reports_corruption_is_not_kept"],
    ),
    (
        "no-cleanup-on-failure",
        shell("trap cleanup EXIT", ":"),
        ["test_corrupt_source_fails_and_leaves_no_debris"],
    ),
    (
        "cp-instead-of-dot-backup",
        shell(SNAPSHOT, 'cp -- "$DB" "$tmp" || die "copy failed"'),
        ["test_snapshot_is_consistent_while_a_writer_holds_an_open_transaction"],
    ),
    (
        "missing-source-unchecked",
        [
            (
                SHELL,
                '[[ -f $DB ]] || die "源库不存在或不是普通文件：$DB"\n[[ -r $DB ]] || die "源库读不了：$DB"',
                ":",
            ),
            (
                "backend-dawn/app/jvm/src/main.dawn",
                '    match io.with_fs_real(() => io.file_size(db_path)) { Ok(_) -> ()\n      Err(e) -> panic("existing database required: ${e.message}") }',
                "    ()",
            ),
            (
                "backend-dawn/app/native/src/main.dawn",
                '  if mode != "--migrate" {',
                '  if mode != "--migrate" && mode != "--backup" {',
            ),
        ],
        ["test_missing_source_database_fails"],
    ),
    (
        "product-not-gzipped",
        shell('mv -- "$tmp.gz" "$final"', 'mv -- "$tmp" "$final"'),
        [
            "test_product_restores_to_the_same_database",
            "test_snapshot_is_consistent_while_a_writer_holds_an_open_transaction",
        ],
    ),
    (
        "product-mode-not-tightened",
        shell('chmod 600 -- "$tmp.gz"', 'chmod 644 -- "$tmp.gz"'),
        ["test_permissions_are_tight"],
    ),
    (
        "dest-mode-not-tightened",
        shell('chmod 700 -- "$DEST"', 'chmod 755 -- "$DEST"'),
        ["test_permissions_are_tight"],
    ),
    (
        "sha256-of-the-wrong-file",
        shell('sha256sum -- "$name"', 'sha256sum -- "$tmp"'),
        ["test_sha256_sidecar_matches_the_product"],
    ),
    (
        "bad-keep-accepted",
        shell('((KEEP >= 1)) || die "KEEP 至少是 1，收到：$KEEP"', ":"),
        ["test_bad_keep_is_rejected_before_anything_is_deleted"],
    ),
    (
        "system-sqlite-restored",
        shell(
            SNAPSHOT,
            '''sqlite3 "$DB" ".backup '$tmp'" || die "system SQLite backup failed"''',
        ),
        ["test_system_sqlite_is_never_invoked"],
    ),
    (
        "low-engine-accepted",
        [
            (CORE, "if not safe_version(version)", "if false"),
            (JVM, "if not safe_version(version)", "if false"),
        ],
        ["test_low_engine_is_refused"],
    ),
    (
        "source-written-during-backup",
        [
            (
                CORE,
                '  execute("VACUUM main INTO ?", [PStr(dest)])?',
                '  execute("create table main.backup_write_probe(v integer)", [])?\n  execute("VACUUM main INTO ?", [PStr(dest)])?',
            )
        ],
        ["test_backup_does_not_migrate_or_change_the_source"],
    ),
    (
        "late-wal-commit-included",
        shell(
            'gzip -c -- "$tmp" >"$tmp.gz"',
            'gzip -c -- "$tmp" >"$tmp.gz"\nrm -- "$tmp"\n'
            + SNAPSHOT
            + '\ngzip -c -- "$tmp" >"$tmp.gz"',
        ),
        ["test_wal_commit_after_snapshot_is_not_in_the_copy"],
    ),
    (
        "fts-external-content-unchecked",
        [(CORE, "values('integrity-check',1)", "values('integrity-check',0)")],
        ["test_fts_integrity_checks_external_content"],
    ),
]


def stage(work, name, changes):
    tree = work / name
    for rel in ["backend-dawn/core", "backend-dawn/app", "deploy/backup-db.sh"]:
        source, target = REPO / rel, tree / rel
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    (tree / ".mutant-workdir").write_text(name)
    GUARD.require_workdir(tree, root=REPO)
    for rel, before, after in changes:
        path = tree / rel
        text = path.read_text()
        assert text.count(before) == 1, (name, rel, text.count(before), before)
        path.write_text(text.replace(before, after, 1))
        print(f"  anchor {rel}: {before.splitlines()[0]}", flush=True)
    return tree


def pytest_run(work, env, script, owners=()):
    report = work / "pytest.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_backup_db.py",
            "-q",
            "--tb=short",
            "-p",
            "no:cacheprovider",
            "--junitxml",
            str(report),
            *(["-k", " or ".join(owners)] if owners else []),
        ],
        cwd=REPO / "backend",
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, **env, "BACKUP_TEST_SCRIPT": str(script)},
    )
    (work / "pytest.log").write_text(result.stdout + result.stderr)
    assert result.returncode in (0, 1), result.stdout + result.stderr
    assert "ERROR " not in result.stdout, result.stdout + result.stderr
    failed = set(re.findall(r"^FAILED .*::(\w+)", result.stdout, re.M))
    assert failed == set(owners), (failed, owners, result.stdout + result.stderr)
    if owners:
        # 两臂都必须击中；宽泛的 owner 仍允许其阳性参数（如 FTS 未损坏）为绿。
        for owner in owners:
            for arm in ("jvm", "native"):
                assert re.search(
                    r"^FAILED .*::" + owner + r"\[" + arm, result.stdout, re.M
                ), result.stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.splitlines()[-1]


def unit_controls(work):
    tests = runtime.module(
        "backup_test_project", REPO / "backend-dawn/scripts/test_project.py"
    )
    dawn = runtime.run([REPO / "scripts/fetch-dawn.sh"]).stdout.strip()
    for name, before, after, label in [
        (
            "integrity-sql-error-ignored",
            '  let rows = query("PRAGMA backup_copy.integrity_check", [], [ColStr])?',
            '  let rows = match query("PRAGMA backup_copy.integrity_check", [], [ColStr]) { Ok(v) -> v\n    Err(_) -> [[VStr("ok")]] }',
            "backup propagates copy integrity SQL errors",
        ),
        (
            "integrity-result-ignored",
            INTEGRITY_RESULT,
            "  let _ = rows",
            "backup requires the complete integrity result to be exactly ok",
        ),
    ]:
        tree = stage(work, name, [(CORE, before, after)])
        project = tree / "unit"
        (project / "src/db").mkdir(parents=True)
        (project / "dawn.toml").write_text('schema = 1\nname = "backup_controls"\n')
        shutil.copy2(
            REPO / "backend-dawn/core/src/db/sql.dawn", project / "src/db/sql.dawn"
        )
        (project / "src/backup.dawn").write_text(
            tests.combine(
                (tree / CORE).read_text(),
                (REPO / "backend-dawn/test/core/backup.dawn").read_text(),
            )
        )
        result = subprocess.run(
            [dawn, "test", str(project)], capture_output=True, text=True, timeout=180
        )
        (tree / "unit.log").write_text(result.stdout + result.stderr)
        assert result.returncode == 1 and f"FAIL  {label}" in result.stdout, (
            result.stdout + result.stderr
        )
        print(f"RED {name}: compiled; {label}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--work", type=Path, required=False)
    args = parser.parse_args()
    if args.list:
        for name, _, owners in MUTANTS:
            print(name, ",".join(owners))
        return
    assert args.work, "--work must name a new disposable evidence directory"
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    assert not any(work.iterdir()), "work directory must be empty"
    started = time.monotonic()
    env = runtime.prepare(work / "runtime")
    saved = work / "runtime/runtime.json"
    env["BACKUP_TEST_RUNTIME"] = str(saved)
    chosen = [case for case in MUTANTS if not args.only or case[0] in args.only]
    assert len(chosen) == (len(set(args.only)) if args.only else len(MUTANTS))
    for name, changes, owners in chosen:
        tree = stage(work, name, changes)
        current = dict(env)
        if any(rel != SHELL for rel, _, _ in changes):
            current.update(runtime.build_entries(tree / "backend-dawn", tree / "built"))
            runtime_path = tree / "runtime.json"
            runtime_path.write_text(json.dumps(current))
            current["BACKUP_TEST_RUNTIME"] = str(runtime_path)
        summary = pytest_run(tree, current, tree / SHELL, owners)
        print(f"RED {name}: {summary}", flush=True)
    if not args.only:
        unit_controls(work)
    print(
        "PASS unmodified positive control: " + pytest_run(work, env, REPO / SHELL),
        flush=True,
    )
    print(
        f"PASS {len(chosen)} backup mutations; unit controls={0 if args.only else 2}; wall_s={time.monotonic() - started:.3f}"
    )


if __name__ == "__main__":
    main()
