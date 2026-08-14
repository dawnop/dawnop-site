#!/usr/bin/env python3
"""`check_server_drift.py` 的负控：每条判词配一个能让它转红的 production mutant。

漂移检查器是个「平时永远输出 OK」的东西，而永远输出 OK 的东西和坏掉了长得一模一样。
这个脚本把每个 mutant 打进生产文件，跑一遍 `backend/tests/test_server_drift.py`，
要求至少有一个测试红了、且红的恰好是预期的那些。

    scripts/server_drift_mutants.py           # 全部
    scripts/server_drift_mutants.py --list
    scripts/server_drift_mutants.py --only drift-ignores-missing-on-server

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
TEST_FILE = "tests/test_server_drift.py"
DRIFT = REPO / "scripts" / "check_server_drift.py"


def sub(old: str, new: str) -> Callable[[str], str]:
    def apply(text: str) -> str:
        if old not in text:
            raise SystemExit(f"mutant 的锚点没了，请更新脚本：{old!r}")
        return text.replace(old, new, 1)

    return apply


MISSING_LOOP = """        for name in sorted(set(exp) - set(act)):
            problems.append(f"{group}: 服务器上缺 {name}")
"""

EXTRA_LOOP = """        for name in sorted(set(act) - set(exp)):
            problems.append(f"{group}: 服务器上多出 {name}（仓库里没有）")
"""

CONTENT_LOOP = """        for name in sorted(set(exp) & set(act)):
            if exp[name] != act[name]:
"""

# (名字, 说明, 变形, 预期转红的测试函数名)
MUTANTS: list[tuple[str, str, Callable[[str], str], set[str]]] = [
    (
        "drift-ignores-missing-on-server",
        "不再报「仓库有、服务器没有」（#236 那三个文件走的就是这条）",
        sub(MISSING_LOOP, ""),
        {"test_missing_on_server_is_reported"},
    ),
    (
        "drift-ignores-extra-on-server",
        "不再报「服务器有、仓库没有」（陈旧 rollback 脚本副本走的是这条）",
        sub(EXTRA_LOOP, ""),
        # stale-scripts 那组的期望是空表，所以它**只**靠这个方向被发现；
        # 「服务器独有的组」也必然一起红，因为那个组的每一项都是「多出」。
        {
            "test_extra_on_server_is_reported",
            "test_a_group_only_the_server_knows_about_still_gets_compared",
        },
    ),
    (
        "drift-only-walks-expected-groups",
        "只遍历仓库知道的组，服务器报上来的新组被静默丢掉",
        sub("sorted(set(expected) | set(actual))", "sorted(expected)"),
        {"test_a_group_only_the_server_knows_about_still_gets_compared"},
    ),
    (
        "drift-compares-names-not-content",
        "只比名字不比内容（User=dawn vs User=dawnop 就这样溜过去）",
        sub(CONTENT_LOOP, "        for name in []:\n            if False:\n"),
        {"test_same_name_different_content_is_reported"},
    ),
    (
        "drift-always-complains",
        "一致时也抱怨一句——天天喊狼来了等于没有检查器",
        sub(
            "    problems: list[str] = []\n",
            '    problems: list[str] = ["something"]\n',
        ),
        # 五条一起红，而这正是想要的：三个方向的判词都写着 `len(problems) == 1`，
        # 凭空多一条就把它们全打破。想让这个 mutant「只红一条」，只能把那三条的
        # 计数断言松成「消息在列表里」，那等于放行「顺带多报几条」，得不偿失。
        {
            "test_identical_sides_report_nothing",
            "test_missing_on_server_is_reported",
            "test_extra_on_server_is_reported",
            "test_same_name_different_content_is_reported",
            "test_a_group_only_the_server_knows_about_still_gets_compared",
        },
    ),
    (
        "remote-script-keeps-pycache",
        "远端不排除 __pycache__/*.pyc，55 个 .pyc 变成 55 处假漂移",
        sub("! -path '*/__pycache__/*' ! -name '*.pyc' ", ""),
        {"test_remote_script_excludes_pycache"},
    ),
    (
        "repo-side-keeps-the-backend-prefix",
        "仓库侧的键带上 backend/ 前缀，与服务器布局对不上（全缺 + 全多）",
        sub(
            'expected["fastapi-app"][rel[len("backend/") :]]',
            'expected["fastapi-app"][rel]',
        ),
        # 「三个文件在不在 manifest 里」那条也会红：它按服务器布局的键去找，
        # 前缀一变就都找不到。两条查的是同一件事的两面，一起红是对的。
        {
            "test_repo_side_maps_backend_app_onto_the_server_layout",
            "test_the_three_files_that_were_missing_in_production_are_in_the_manifest",
        },
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
            print(f"{name:<38} {why}")
        return 0

    chosen = [m for m in MUTANTS if not args.only or m[0] in args.only]
    if args.only and len(chosen) != len(set(args.only)):
        raise SystemExit(f"--only 里有不存在的名字：{args.only}")

    print("== 基线（未变异）==")
    if run_tests():
        raise SystemExit("   基线就是红的，先修好再跑变异体")
    print("   全绿\n")

    original = DRIFT.read_text(encoding="utf-8")
    bad = 0
    try:
        for name, why, mutate, want in chosen:
            DRIFT.write_text(mutate(original), encoding="utf-8")
            got = run_tests()
            DRIFT.write_text(original, encoding="utf-8")
            ok = got == want
            bad += 0 if ok else 1
            print(f"[{'RED ' if ok else 'BAD '}] {name}: {why}")
            print(f"        期望转红 {sorted(want)}")
            print(f"        实测转红 {sorted(got)}")
            if not got:
                print("        !!! 一条都没红：这条判词没有守住任何东西")
            print()
    finally:
        DRIFT.write_text(original, encoding="utf-8")

    if bad:
        print(f"{bad} 个 mutant 的实测与期望不符。")
        return 1
    print(f"全部 {len(chosen)} 个 mutant 都只红了它该红的那些。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
