#!/usr/bin/env python3
"""守卫：提交信息里不得出现 Claude 署名。

约定见 CLAUDE.md 第 7 节——`Co-Authored-By: Claude` 与 `Claude-Session: <url>`
两种 trailer 都不要。源头已在 Claude Code 的 settings.json 关掉（attribution.commit
置空 + sessionUrl=false），但那是「这台机器、这次会话」的设置：换台开发机、或某个会话的
系统提示坚持要加，源头就压不住。2026-07-17 就为此 force-push 清过 20 条。这个脚本是那层
兜底——`commit-msg` hook 在本地提交时拦，CI 在推上来后再拦一次（hook 是每台机器手动
opt-in 的，靠不住，所以 CI 那一半不是冗余）。

两种用法，对应两个调用点：
  check-no-claude-trailer.py --commit-msg <file>   # 单条待提交信息（commit-msg hook）
  check-no-claude-trailer.py --range <A..B>        # 一段提交范围的所有信息（CI）
"""

import argparse
import re
import subprocess
import sys

# Co-Authored-By 只拦 Claude / Anthropic 的——真人协作者的署名是合法的，不碰。
PATTERNS = [
    re.compile(r"^\s*Claude-Session\s*:", re.I),
    re.compile(r"^\s*Co-authored-by\s*:.*(claude|anthropic)", re.I),
]


def offenders(msg: str) -> list[str]:
    """返回信息里命中的原样行（去首尾空白）。"""
    hits = []
    for line in msg.splitlines():
        if any(p.search(line) for p in PATTERNS):
            hits.append(line.strip())
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--commit-msg", metavar="FILE", help="待提交信息文件（commit-msg hook 传入）"
    )
    src.add_argument(
        "--range", metavar="A..B", help="git rev-range，检查其中每条提交信息"
    )
    args = ap.parse_args()

    problems = []  # (标签, 命中行)
    if args.commit_msg:
        with open(args.commit_msg, encoding="utf-8") as f:
            for line in offenders(f.read()):
                problems.append(("待提交", line))
    else:
        # %x1f 分隔 sha 与信息，%x00 分隔每条提交——两者都不会出现在提交信息里。
        out = subprocess.run(
            ["git", "log", "--format=%H%x1f%B%x00", args.range],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for rec in out.split("\x00"):
            rec = rec.strip("\n")
            if not rec:
                continue
            sha, _, msg = rec.partition("\x1f")
            for line in offenders(msg):
                problems.append((sha[:8], line))

    if problems:
        sys.stderr.write("提交信息含 Claude 署名（CLAUDE.md 第 7 节：绝不添加）：\n")
        for label, line in problems:
            sys.stderr.write(f"  {label}: {line}\n")
        sys.stderr.write("\n删掉这些 trailer 再提交；已进历史的用 git rebase 清除。\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
