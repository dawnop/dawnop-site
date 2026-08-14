#!/usr/bin/env python3
"""`deploy/backup-db.sh` 的负控：每条判词配一个能让它转红的 production mutant。

备份脚本平时的输出永远是「写了一份，删了 0 个」，而永远成功的东西和没在做事
长得一模一样。这个脚本把每个 mutant 打进生产文件，跑一遍
`backend/tests/test_backup_db.py`，要求红的**恰好是**预期的那些。

    scripts/backup_db_mutants.py          # 全部
    scripts/backup_db_mutants.py --list
    scripts/backup_db_mutants.py --only rotate-glob-too-wide

不进 CI（它临时改生产文件，跑完还原）。跑之前树要干净，中断时按 git 还原。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
TEST_FILE = "tests/test_backup_db.py"
SCRIPT = REPO / "deploy" / "backup-db.sh"


def sub(old: str, new: str) -> Callable[[str], str]:
    def apply(text: str) -> str:
        if old not in text:
            raise SystemExit(f"mutant 的锚点没了，请更新脚本：{old!r}")
        return text.replace(old, new, 1)

    return apply


GUARD_MISSING_SOURCE = """[[ -f $DB ]] || die "源库不存在或不是普通文件：$DB"
[[ -r $DB ]] || die "源库读不了：$DB"
"""

SNAPSHOT = (
    """sqlite3 "$DB" ".backup '$tmp'" || die "取快照失败（源库可能已损坏）：$DB\""""
)

INTEGRITY_RUN = (
    "check=$(sqlite3 \"$tmp\" 'PRAGMA integrity_check;' 2>&1) "
    '|| die "快照校验跑不起来：$check"\n'
)

INTEGRITY_RESULT = (
    '[[ $check == ok ]] || die "快照没通过 integrity_check，不保留：'
    "${check%%$'\\n'*}\"\n"
)

CANDIDATES = """for f in "$DEST"/dawnop-*.db.gz; do
  base=${f##*/}
  [[ $base =~ ^dawnop-[0-9]{8}T[0-9]{6}Z\\.db\\.gz$ ]] || continue
  existing+=("$base")
done"""

CANDIDATES_WIDE = """for f in "$DEST"/*; do
  base=${f##*/}
  existing+=("$base")
done"""

# (名字, 说明, 变形, 预期转红的测试函数名)
MUTANTS: list[tuple[str, str, Callable[[str], str], set[str]]] = [
    (
        "rotate-keeps-everything",
        "轮转一个都不删——磁盘慢慢满，然后某天备份和后端一起写不下去",
        sub("if ((${#existing[@]} > KEEP)); then", "if false; then"),
        # 「外来文件没被动」那条也会红：它同时钉着「产物恰好剩 KEEP 份」。
        # 把那半条拿掉就等于放行「一个都不删」，不划算。
        {
            "test_rotation_keeps_exactly_the_newest_keep",
            "test_rotation_does_not_touch_foreign_files",
        },
    ),
    (
        "rotate-glob-too-wide",
        "候选集从「自己的命名模式」放宽成整个目录，外来文件被卷进轮转",
        sub(CANDIDATES, CANDIDATES_WIDE),
        {
            "test_rotation_does_not_touch_foreign_files",
            # 宽 glob 把 .sha256 旁文件也算进候选，于是「剩几个」的账也错了。
            # 两条查的是同一件事的两面，一起红是对的。
            "test_rotation_keeps_exactly_the_newest_keep",
        },
    ),
    (
        "rotate-keep-off-by-one",
        "多留一份。数量判词写成「至少 KEEP 份」就抓不到它",
        sub(
            '"${sorted[@]:0:${#sorted[@]} - KEEP}"',
            '"${sorted[@]:0:${#sorted[@]} - KEEP - 1}"',
        ),
        {
            "test_rotation_keeps_exactly_the_newest_keep",
            "test_rotation_does_not_touch_foreign_files",
        },
    ),
    (
        "skip-integrity-check",
        "整段不校验副本，坏页照单全收——能解压、打不开的备份比没有更坏",
        sub(INTEGRITY_RUN + INTEGRITY_RESULT, "check=ok\n"),
        {
            "test_snapshot_that_cannot_be_integrity_checked_is_not_kept",
            "test_snapshot_that_reports_corruption_is_not_kept",
        },
    ),
    (
        "integrity-result-not-checked",
        "只看 sqlite3 的退出码、不看它说了什么——有一类损坏它是退 0 报出来的",
        sub(INTEGRITY_RESULT, ""),
        # 只有「命令退 0、坏消息在输出里」那条会红；另一条靠退出码就挡住了。
        {"test_snapshot_that_reports_corruption_is_not_kept"},
    ),
    (
        "no-cleanup-on-failure",
        "失败路径不清理临时文件，sqlite3 留下的 0 字节残骸一天攒一个",
        sub("trap cleanup EXIT", ":"),
        {"test_corrupt_source_fails_and_leaves_no_debris"},
    ),
    (
        "cp-instead-of-dot-backup",
        "用 cp 拷主库文件——活库上这拿到的不是任何一个时刻的库",
        sub(SNAPSHOT, 'cp -- "$DB" "$tmp" || die "取快照失败（源库可能已损坏）：$DB"'),
        {"test_snapshot_is_consistent_while_a_writer_holds_an_open_transaction"},
    ),
    (
        "missing-source-unchecked",
        "不检查源库在不在，交给 sqlite3——它会建个空库然后备份它",
        sub(GUARD_MISSING_SOURCE, ""),
        {"test_missing_source_database_fails"},
    ),
    (
        "product-not-gzipped",
        "把未压缩的快照按 .db.gz 的名字挪过去，解压那一步才会发现",
        sub('mv -- "$tmp.gz" "$final"', 'mv -- "$tmp" "$final"'),
        {
            "test_product_restores_to_the_same_database",
            "test_snapshot_is_consistent_while_a_writer_holds_an_open_transaction",
        },
    ),
    (
        "product-mode-not-tightened",
        "产物留 644——备份里有 bcrypt 口令哈希和全部草稿",
        sub('chmod 600 -- "$tmp.gz"', 'chmod 644 -- "$tmp.gz"'),
        {"test_permissions_are_tight"},
    ),
    (
        "dest-mode-not-tightened",
        "备份目录留 755，同机上谁都能列目录、取走整份库",
        sub('chmod 700 -- "$DEST"', 'chmod 755 -- "$DEST"'),
        {"test_permissions_are_tight"},
    ),
    (
        "sha256-of-the-wrong-file",
        "旁文件记的是未压缩快照的哈希，校验产物永远对不上",
        sub('sha256sum -- "$name"', 'sha256sum -- "$tmp"'),
        {"test_sha256_sidecar_matches_the_product"},
    ),
    (
        "bad-keep-accepted",
        "KEEP=0 也照跑：轮转会把刚写出来的那一份一起删掉",
        sub('((KEEP >= 1)) || die "KEEP 至少是 1，收到：$KEEP"', ":"),
        {"test_bad_keep_is_rejected_before_anything_is_deleted"},
    ),
]


def run_tests() -> set[str]:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            TEST_FILE,
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
    )
    failed = {
        m.group(1)
        for line in proc.stdout.splitlines()
        if (m := re.match(r"FAILED .*::([A-Za-z0-9_]+)", line))
    }
    if proc.returncode not in (0, 1):
        print(proc.stdout[-3000:])
        raise SystemExit("pytest 以非 0/1 退出，mutant 打坏了别的东西")
    return failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for name, why, _, _ in MUTANTS:
            print(f"{name:<32} {why}")
        return 0

    chosen = [m for m in MUTANTS if not args.only or m[0] in args.only]
    if args.only and len(chosen) != len(set(args.only)):
        raise SystemExit(f"--only 里有不存在的名字：{args.only}")

    print("== 基线（未变异）==")
    if run_tests():
        raise SystemExit("   基线就是红的，先修好再跑变异体")
    print("   全绿\n")

    original = SCRIPT.read_text(encoding="utf-8")
    mode = SCRIPT.stat().st_mode
    bad = 0
    try:
        for name, why, mutate, want in chosen:
            SCRIPT.write_text(mutate(original), encoding="utf-8")
            SCRIPT.chmod(mode)
            got = run_tests()
            SCRIPT.write_text(original, encoding="utf-8")
            SCRIPT.chmod(mode)
            ok = got == want
            bad += 0 if ok else 1
            print(f"[{'RED ' if ok else 'BAD '}] {name}: {why}")
            print(f"        期望转红 {sorted(want)}")
            print(f"        实测转红 {sorted(got)}")
            if not got:
                print("        !!! 一条都没红：这条判词没有守住任何东西")
            print()
    finally:
        SCRIPT.write_text(original, encoding="utf-8")
        SCRIPT.chmod(mode)

    if bad:
        print(f"{bad} 个 mutant 的实测与期望不符。")
        return 1
    print(f"全部 {len(chosen)} 个 mutant 都只红了它该红的那些。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
