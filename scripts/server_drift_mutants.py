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

BACKUP_UNITS_EMIT = """for f in /etc/systemd/system/dawnop-backup.service /etc/systemd/system/dawnop-backup.timer; do
  [ -f "$f" ] && emit unit:dawnop-backup "${f#/etc/systemd/system/}" "$(sha "$f")"
done
"""

OPT_SCRIPTS_EMIT = """for f in /opt/dawnop/*.sh; do
  [ -e "$f" ] || continue
  emit opt-scripts "${f#/opt/dawnop/}" "$(sha "$f")"
done
"""

FROM_DEPLOY_LOOP = '            expected[group][name] = sha256_of(REPO / "deploy" / name)'

# (名字, 说明, 变形, 预期转红的测试函数名)
MUTANTS: list[tuple[str, str, Callable[[str], str], set[str]]] = [
    (
        "drift-ignores-missing-on-server",
        "不再报「仓库有、服务器没有」（#236 那三个文件走的就是这条）",
        sub(MISSING_LOOP, ""),
        # 备份那条也红：timer 没装上去走的正是这个方向。
        {
            "test_missing_on_server_is_reported",
            "test_backup_timer_missing_on_server_is_reported",
        },
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
            "test_an_unknown_script_under_opt_dawnop_is_reported",
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
        # 服务器上被就地改过的 backup-db.sh 也只能靠这个方向被发现。
        {
            "test_same_name_different_content_is_reported",
            "test_backup_script_edited_on_the_server_is_reported",
        },
    ),
    (
        "drift-always-complains",
        "一致时也抱怨一句——天天喊狼来了等于没有检查器",
        sub(
            "    problems: list[str] = []\n",
            '    problems: list[str] = ["something"]\n',
        ),
        # 八条一起红，而这正是想要的：每个方向的判词都钉死了「恰好一条差异」，
        # 凭空多一条就把它们全打破。想让这个 mutant「只红一条」，只能把它们的
        # 计数断言松成「消息在列表里」，那等于放行「顺带多报几条」，得不偿失。
        {
            "test_identical_sides_report_nothing",
            "test_missing_on_server_is_reported",
            "test_extra_on_server_is_reported",
            "test_same_name_different_content_is_reported",
            "test_a_group_only_the_server_knows_about_still_gets_compared",
            "test_backup_timer_missing_on_server_is_reported",
            "test_an_unknown_script_under_opt_dawnop_is_reported",
            "test_backup_script_edited_on_the_server_is_reported",
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
    (
        "backup-group-not-registered",
        "manifest 里没有 unit:dawnop-backup 这一组（= #263 装的东西没人盯）",
        sub('    "unit:dawnop-backup": "库备份的 service + timer（#263）",\n', ""),
        # repo_side 往一个不存在的组里写，KeyError 当场炸——凡是取 manifest 的判词
        # 全红。这一片红是对的：一组没登记，它的每一条结论都不再成立。
        # 丢 opt-scripts 是同一形状（对称），不另设变异体。
        {
            "test_the_backup_pieces_installed_by_263_are_in_the_manifest",
            "test_repo_side_strips_the_deploy_prefix_for_installed_files",
            "test_backup_timer_missing_on_server_is_reported",
            "test_an_unknown_script_under_opt_dawnop_is_reported",
            "test_backup_script_edited_on_the_server_is_reported",
            "test_repo_side_maps_backend_app_onto_the_server_layout",
            "test_the_three_files_that_were_missing_in_production_are_in_the_manifest",
        },
    ),
    (
        "backup-keys-keep-the-deploy-prefix",
        "装机文件的键留着 deploy/ 前缀，与服务器上的基名对不上（全缺 + 全多）",
        sub("expected[group][name] =", 'expected[group]["deploy/" + name] ='),
        # 「服务器上多出」那条不红，而且不该红：它加的是一个仓库两边都没有的名字，
        # 前缀怎么削都照报。它守的是别的东西。
        {
            "test_the_backup_pieces_installed_by_263_are_in_the_manifest",
            "test_repo_side_strips_the_deploy_prefix_for_installed_files",
            "test_backup_timer_missing_on_server_is_reported",
            "test_backup_script_edited_on_the_server_is_reported",
        },
    ),
    (
        "backup-hashes-the-wrong-repo-file",
        "键对、哈希取自 deploy/ 下另一个文件——比对的不再是这三样的正本",
        sub(FROM_DEPLOY_LOOP, FROM_DEPLOY_LOOP.replace("name)", '"deploy-fastapi.sh")')),
        # 只有「哈希得等于正本」那条能看见：键没变，三个方向的判词两边同源，照绿。
        {"test_the_backup_pieces_installed_by_263_are_in_the_manifest"},
    ),
    (
        "remote-forgets-the-backup-units",
        "远端不 emit dawnop-backup 的 service/timer，那一组恒等于「服务器上什么都没有」",
        sub(BACKUP_UNITS_EMIT, ""),
        {"test_every_manifest_group_is_emitted_by_the_remote_script"},
    ),
    (
        "remote-forgets-opt-scripts",
        "远端不 emit /opt/dawnop/*.sh，装机脚本被改了也看不见",
        sub(OPT_SCRIPTS_EMIT, ""),
        {"test_every_manifest_group_is_emitted_by_the_remote_script"},
    ),
]


def run_tests() -> tuple[set[str], int]:
    """返回 (红了的测试名, 绿了的条数)。

    第二个数不是装饰：pytest 压根没跑起来（比如没 source 那个 venv）时，stdout 是空的，
    「一条都没红」和「全绿」长得一模一样——这个脚本自己就差点这么骗过去。
    """
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
        print(proc.stderr[-2000:])
        raise SystemExit("pytest 以非 0/1 退出，mutant 打坏了别的东西")
    m = re.search(r"(\d+) passed", proc.stdout)
    return failed, int(m.group(1)) if m else 0


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
    failed, passed = run_tests()
    if failed:
        raise SystemExit("   基线就是红的，先修好再跑变异体")
    if not passed:
        raise SystemExit(
            "   一条都没跑起来（pytest 在这个解释器里吗？"
            "先 source backend/.venv/bin/activate）"
        )
    print(f"   全绿（{passed} 条）\n")

    original = DRIFT.read_text(encoding="utf-8")
    bad = 0
    try:
        for name, why, mutate, want in chosen:
            DRIFT.write_text(mutate(original), encoding="utf-8")
            got, _ = run_tests()
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
