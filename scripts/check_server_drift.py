#!/usr/bin/env python3
"""服务器上装着的东西，必须等于仓库里写着的东西。

**为什么存在。** 2026-08-14 一天之内查出三处漂移，全是同一个形状：

  1. `/opt/dawnop-dawn/rollback-to-fastapi.sh` 是 7-16 的 50 行旧版，仓库里那份 414 行；
  2. 装机的 `dawnop-backend.service` 写 `User=dawnop`，仓库副本写 `User=dawn`；
  3. `/opt/dawnop/backend/app` 停在 7-06，`#236` 加的三个文件从来没上过生产。

第 2 条尤其说明问题：那里**有**一道门禁（`test_fastapi_identity_constants_match_the_systemd_unit`
要求脚本常量等于单元文件的 `User=`），而且一直是绿的。它的 oracle 是**仓库的**单元文件，
于是仓库和脚本一起漂的时候，它照绿。**没有任何东西比对过「仓库里的」和「装机的」。**
这个脚本就是那个缺失的比对器。

**为什么不进 CI。** 它要 ssh 上生产。CI 里没有那台机器，也不该有。它是运维工具：
发版后、演练前、以及任何「我以为服务器上是新的」的时刻手动跑。可测的那部分（`compare`）
是纯函数，`backend/tests/test_server_drift.py` 用假的两张表喂它，
`scripts/server_drift_mutants.py` 给每条判词配了能转红的变异体。

    scripts/check_server_drift.py                    # 比对，有差异就 exit 1
    scripts/check_server_drift.py --host dawn@host   # 换机器
    scripts/check_server_drift.py --print-remote     # 只打印将要在服务器上跑的那段 shell
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 每组：(组名, 说明, 仓库侧怎么算, 服务器侧那边是什么)
#
# 组名同时是 remote 脚本里的第一列。加一组要同时改这里和 REMOTE_SCRIPT，
# test_every_manifest_group_is_emitted_by_the_remote_script 会追问。
GROUPS = {
    "unit:dawnop-backend": "FastAPI 单元（回滚目标）",
    "unit:dawnop-dawn": "Dawn 单元（生产）",
    "fastapi-app": "FastAPI 应用代码（回滚目标跑的就是它）",
    "stale-scripts": "/opt/dawnop-dawn 下不该有任何 .sh",
}

# 服务器侧只做一件事：把「有什么、各自的 sha256 是多少」打出来。判断全在本地做，
# 因为判断逻辑要能被测试喂假数据，而 ssh 那头喂不了。
#
# 输出每行三列，tab 分隔：<组名> <相对名字> <sha256>。
# `sha256sum <"$f"` 从 stdin 读，于是输出里没有文件名可以混进第二列。
REMOTE_SCRIPT = r"""
set -u
emit() { printf '%s\t%s\t%s\n' "$1" "$2" "$3"; }
sha() { sha256sum <"$1" | cut -d' ' -f1; }

for u in dawnop-backend dawnop-dawn; do
  f="/etc/systemd/system/$u.service"
  [ -f "$f" ] && emit "unit:$u" "$u.service" "$(sha "$f")"
done

if [ -d /opt/dawnop/backend/app ]; then
  find /opt/dawnop/backend/app -type f \
       ! -path '*/__pycache__/*' ! -name '*.pyc' -print0 |
    LC_ALL=C sort -z |
    while IFS= read -r -d '' f; do
      emit fastapi-app "${f#/opt/dawnop/backend/}" "$(sha "$f")"
    done
fi

for f in /opt/dawnop-dawn/*.sh; do
  [ -e "$f" ] || continue
  emit stale-scripts "${f#/opt/dawnop-dawn/}" "$(sha "$f")"
done
"""


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tracked_files(prefix: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", prefix],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def repo_side() -> dict[str, dict[str, str]]:
    """仓库这边**应该**是什么样。键与 REMOTE_SCRIPT 的第二列对齐。"""
    expected: dict[str, dict[str, str]] = {g: {} for g in GROUPS}

    expected["unit:dawnop-backend"]["dawnop-backend.service"] = sha256_of(
        REPO / "deploy" / "dawnop-backend.service"
    )
    expected["unit:dawnop-dawn"]["dawnop-dawn.service"] = sha256_of(
        REPO / "backend-dawn" / "deploy" / "dawnop-dawn.service"
    )
    for rel in tracked_files("backend/app"):
        # 服务器上 /opt/dawnop/backend/app/... 对应仓库 backend/app/...
        expected["fastapi-app"][rel[len("backend/") :]] = sha256_of(REPO / rel)

    # stale-scripts 故意留空：这一组的期望就是「什么都没有」，
    # 于是服务器上冒出来的任何 .sh 都会走「只在服务器上」那条。
    return expected


def parse_remote(text: str) -> dict[str, dict[str, str]]:
    actual: dict[str, dict[str, str]] = {g: {} for g in GROUPS}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise SystemExit(f"服务器返回的行不是三列：{line!r}")
        group, name, digest = parts
        actual.setdefault(group, {})[name] = digest
    return actual


def compare(
    expected: dict[str, dict[str, str]], actual: dict[str, dict[str, str]]
) -> list[str]:
    """返回差异清单，空列表 = 服务器与仓库一致。

    三个方向都要报，缺一条就有一整类漂移是隐形的：
      * 仓库有服务器没有 → 这次真实故障里 `#236` 那三个文件走的就是这条；
      * 服务器有仓库没有 → 陈旧的 rollback 脚本副本走这条；
      * 两边都有但内容不同 → `User=dawn` vs `User=dawnop` 走这条。
    """
    problems: list[str] = []
    for group in sorted(set(expected) | set(actual)):
        exp = expected.get(group, {})
        act = actual.get(group, {})
        for name in sorted(set(exp) - set(act)):
            problems.append(f"{group}: 服务器上缺 {name}")
        for name in sorted(set(act) - set(exp)):
            problems.append(f"{group}: 服务器上多出 {name}（仓库里没有）")
        for name in sorted(set(exp) & set(act)):
            if exp[name] != act[name]:
                problems.append(
                    f"{group}: {name} 内容不同"
                    f"（仓库 {exp[name][:12]}，服务器 {act[name][:12]}）"
                )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="dawn@dawnop.com")
    ap.add_argument("--print-remote", action="store_true")
    args = ap.parse_args()

    if args.print_remote:
        print(REMOTE_SCRIPT)
        return 0

    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", args.host, "sudo bash -s"],
        input=REMOTE_SCRIPT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"ssh {args.host} 失败（rc={proc.returncode}）")

    problems = compare(repo_side(), parse_remote(proc.stdout))
    if not problems:
        for group, why in GROUPS.items():
            print(f"OK    {group:<24} {why}")
        print("服务器与仓库一致。")
        return 0

    for p in problems:
        print(f"DRIFT {p}")
    print(f"\n共 {len(problems)} 处漂移。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
