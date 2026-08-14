#!/usr/bin/env python3
"""`tests/test_view_counter.py` 的负控：每条判词配一个能让它转红的 production mutant。

「浏览计数没改 updated_at」是个平时永远绿的判词，而永远绿的判词和根本没看的判词
输出一样。这个脚本把每个 mutant 打进**生产文件**，跑一遍那个测试文件，然后要求：
  1. 至少有一个测试红了，且
  2. 红的恰好是预期的那些（判词指着的是它声称守着的东西，不是被别的连坐）。

第一个 mutant `views-bump-touches-updated-at` 就是生产上真实发生过的那个 bug：
回滚演练把流量切回 FastAPI 的几十秒里，十篇文章的 updated_at 被浏览计数刷成了当时。

用法：
    python3 backend/scripts/view_counter_mutants.py            # 全部
    python3 backend/scripts/view_counter_mutants.py --list
    python3 backend/scripts/view_counter_mutants.py --only views-bump-touches-updated-at

不进 CI（它临时改生产文件，跑完还原）。跑之前树要干净，中断时按 git 还原。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
TEST_FILE = "tests/test_view_counter.py"

ARTICLES_API = BACKEND / "app" / "api" / "articles.py"
ARTICLE_MODEL = BACKEND / "app" / "models" / "article.py"


def sub(old: str, new: str) -> Callable[[str], str]:
    def apply(text: str) -> str:
        if old not in text:
            raise SystemExit(f"mutant 的锚点没了，请更新脚本：{old!r}")
        return text.replace(old, new, 1)

    return apply


FIXED_BUMP = """        db.execute(
            update(Article)
            .where(Article.id == article.id)
            .values(views=Article.views + 1, updated_at=Article.updated_at)
        )
"""

# 事故当天生产上跑的就是这三行
BUGGY_BUMP = """        article.views = (article.views or 0) + 1
"""

# (名字, 说明, 目标文件, 变形, 预期转红的测试函数名)
MUTANTS: list[tuple[str, str, Path, Callable[[str], str], set[str]]] = [
    (
        "views-bump-touches-updated-at",
        "把计数改回 ORM 属性赋值（= 生产上真实发生过的那个 bug）",
        ARTICLES_API,
        sub(FIXED_BUMP, BUGGY_BUMP),
        {
            "test_anonymous_read_bumps_views_without_touching_updated_at",
            "test_repeated_reads_never_touch_updated_at",
        },
    ),
    (
        "explicit-updated-at-dropped",
        "SET 子句里不再显式写 updated_at —— onupdate 对 Core update 一样生效",
        ARTICLES_API,
        sub(", updated_at=Article.updated_at)", ")"),
        {
            "test_anonymous_read_bumps_views_without_touching_updated_at",
            "test_repeated_reads_never_touch_updated_at",
        },
    ),
    (
        "updated-at-onupdate-removed",
        "把模型上的 onupdate 删掉——这样「读不改 updated_at」也会绿，但那是拆表不是修",
        ARTICLE_MODEL,
        sub(", onupdate=func.now()", ""),
        {"test_real_edit_still_advances_updated_at"},
    ),
    (
        "views-never-increment",
        "计数不再自增（updated_at 仍不动）——判词不能只盯着时间戳",
        ARTICLES_API,
        sub("views=Article.views + 1", "views=Article.views"),
        {
            "test_anonymous_read_bumps_views_without_touching_updated_at",
            "test_repeated_reads_never_touch_updated_at",
        },
    ),
    (
        "counts-every-read",
        "计数守卫整个没了，草稿预览也算一次浏览",
        ARTICLES_API,
        sub("if user is None and article.published:", "if True:"),
        {
            "test_admin_preview_of_draft_changes_nothing",
            "test_admin_preview_of_published_changes_nothing",
        },
    ),
    (
        "admin-preview-counts",
        "管理员带 token 的预览也计数（已发布那篇会被算进去）",
        ARTICLES_API,
        sub("if user is None and article.published:", "if article.published:"),
        {"test_admin_preview_of_published_changes_nothing"},
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
        # 收集期错误之类：整个文件红了，这不算「某条判词守住了」
        print(proc.stdout[-3000:])
        raise SystemExit("pytest 以非 0/1 退出，mutant 打坏了别的东西")
    return failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for name, why, path, _t, _e in MUTANTS:
            print(f"{name:<30} {path.name:<12} {why}")
        return 0

    chosen = [m for m in MUTANTS if not args.only or m[0] in args.only]
    if args.only and len(chosen) != len(set(args.only)):
        raise SystemExit(f"--only 里有不存在的名字：{args.only}")

    print("== 基线（未变异）==")
    baseline = run_tests()
    if baseline:
        raise SystemExit(f"基线就有红的，先修：{sorted(baseline)}")
    print("   全绿\n")

    bad = []
    for name, why, path, mutate, want in chosen:
        original = path.read_bytes()
        try:
            path.write_text(mutate(original.decode("utf-8")), encoding="utf-8")
            got = run_tests()
        finally:
            path.write_bytes(original)

        ok = got == want
        print(f"[{'RED ' if got else 'GREEN'}] {name}: {why}")
        print(f"        文件 {path.relative_to(REPO)}")
        print(f"        期望转红 {sorted(want)}")
        print(f"        实测转红 {sorted(got)}")
        if not got:
            print("        !!! 一条都没红：这条判词没有守住任何东西")
        if not ok:
            bad.append(name)
            print("        ^^ 不符")
        print()

    if bad:
        print(f"不合格的 mutant：{bad}")
        return 1
    print(f"全部 {len(chosen)} 个 mutant 都只红了它该红的那些。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
