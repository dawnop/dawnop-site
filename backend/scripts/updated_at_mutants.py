#!/usr/bin/env python3
"""「updated_at 不该被顺带写」这一族判词的负控。

规矩（#264）：**只有对该行的显式编辑才推进 `updated_at`；由别处的操作顺带引起的
字段变更不推进。** 三处曾经违反过它：

  - 浏览计数（articles.get_published）——回滚演练时真把十篇文章刷成了当天；
  - 重排导航（pages.reorder）——改的是 nav_order，不是对页面的编辑；
  - 删页面（pages.delete_page）——把旗下文章的 page_id 置空，不是对文章的编辑。

这类判词平时永远绿，而永远绿的判词和根本没看的判词输出一样。这个脚本把每个 mutant
打进**生产文件**，跑一遍下面两个测试文件，然后要求：
  1. 至少有一个测试红了，且
  2. 红的恰好是预期的那些（判词指着的是它声称守着的东西，不是被别的连坐）。

两个文件一起跑：mutant 现在跨文件（删模型上的 onupdate 会打到另一个文件里的判词），
分开跑就看不见这种连坐。期望集写成 `文件名::函数名`，免得两边重名时糊在一起。

用法：
    python3 backend/scripts/updated_at_mutants.py            # 全部
    python3 backend/scripts/updated_at_mutants.py --list
    python3 backend/scripts/updated_at_mutants.py --only views-bump-touches-updated-at

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

VC = "test_view_counter.py"
PG = "test_page_updated_at.py"
TEST_FILES = [f"tests/{VC}", f"tests/{PG}"]

ARTICLES_API = BACKEND / "app" / "api" / "articles.py"
PAGES_API = BACKEND / "app" / "api" / "pages.py"
ARTICLE_MODEL = BACKEND / "app" / "models" / "article.py"
PAGE_MODEL = BACKEND / "app" / "models" / "page.py"


def sub(old: str, new: str) -> Callable[[str], str]:
    def apply(text: str) -> str:
        if old not in text:
            raise SystemExit(f"mutant 的锚点没了，请更新脚本：{old!r}")
        return text.replace(old, new, 1)

    return apply


# ---- 浏览计数（articles.py）----

FIXED_BUMP = """        db.execute(
            update(Article)
            .where(Article.id == article.id)
            .values(views=Article.views + 1, updated_at=Article.updated_at)
        )
"""

# 事故当天生产上跑的就是这行
BUGGY_BUMP = """        article.views = (article.views or 0) + 1
"""

# ---- 重排导航（pages.py）----

FIXED_REORDER = """        db.execute(
            update(Page)
            .where(Page.id == pid)
            .values(nav_order=order, updated_at=Page.updated_at)
        )
"""

BUGGY_REORDER = """        page = db.get(Page, pid)
        if page is not None:
            page.nav_order = order
"""

# ---- 删页面解绑文章（pages.py）----

FIXED_UNBIND = """    db.query(Article).filter(Article.page_id == page.id).update(
        {Article.page_id: None, Article.updated_at: Article.updated_at}
    )
"""

# (名字, 说明, 目标文件, 变形, 预期转红的测试)
MUTANTS: list[tuple[str, str, Path, Callable[[str], str], set[str]]] = [
    (
        "views-bump-touches-updated-at",
        "把计数改回 ORM 属性赋值（= 生产上真实发生过的那个 bug）",
        ARTICLES_API,
        sub(FIXED_BUMP, BUGGY_BUMP),
        {
            f"{VC}::test_anonymous_read_bumps_views_without_touching_updated_at",
            f"{VC}::test_repeated_reads_never_touch_updated_at",
        },
    ),
    (
        "explicit-updated-at-dropped",
        "SET 子句里不再显式写 updated_at —— onupdate 对 Core update 一样生效",
        ARTICLES_API,
        sub(", updated_at=Article.updated_at)", ")"),
        {
            f"{VC}::test_anonymous_read_bumps_views_without_touching_updated_at",
            f"{VC}::test_repeated_reads_never_touch_updated_at",
        },
    ),
    (
        "updated-at-onupdate-removed",
        "把 Article 上的 onupdate 删掉——这样「读不改 updated_at」也会绿，但那是拆表不是修",
        ARTICLE_MODEL,
        sub(", onupdate=func.now()", ""),
        {f"{VC}::test_real_edit_still_advances_updated_at"},
    ),
    (
        "views-never-increment",
        "计数不再自增（updated_at 仍不动）——判词不能只盯着时间戳",
        ARTICLES_API,
        sub("views=Article.views + 1", "views=Article.views"),
        {
            f"{VC}::test_anonymous_read_bumps_views_without_touching_updated_at",
            f"{VC}::test_repeated_reads_never_touch_updated_at",
        },
    ),
    (
        "counts-every-read",
        "计数守卫整个没了，草稿预览也算一次浏览",
        ARTICLES_API,
        sub("if user is None and article.published:", "if True:"),
        {
            f"{VC}::test_admin_preview_of_draft_changes_nothing",
            f"{VC}::test_admin_preview_of_published_changes_nothing",
        },
    ),
    (
        "admin-preview-counts",
        "管理员带 token 的预览也计数（已发布那篇会被算进去）",
        ARTICLES_API,
        sub("if user is None and article.published:", "if article.published:"),
        {f"{VC}::test_admin_preview_of_published_changes_nothing"},
    ),
    (
        "reorder-touches-page-updated-at",
        "重排改回 ORM 属性赋值，拖一下导航顺序就把页面的「最后编辑时间」刷成此刻",
        PAGES_API,
        sub(FIXED_REORDER, BUGGY_REORDER),
        {f"{PG}::test_reorder_does_not_touch_page_updated_at"},
    ),
    (
        "reorder-drops-explicit-updated-at",
        "重排的 SET 子句里不再显式写 updated_at",
        PAGES_API,
        sub(", updated_at=Page.updated_at)", ")"),
        {f"{PG}::test_reorder_does_not_touch_page_updated_at"},
    ),
    (
        "reorder-doesnt-reorder",
        "重排不再真的写 nav_order（时间戳纹丝不动，但功能没了）",
        PAGES_API,
        sub("values(nav_order=order,", "values(nav_order=Page.nav_order,"),
        {f"{PG}::test_reorder_does_not_touch_page_updated_at"},
    ),
    (
        "page-updated-at-onupdate-removed",
        "把 Page 上的 onupdate 删掉——重排那条也会绿，但那是拆表不是修",
        PAGE_MODEL,
        sub(", onupdate=func.now()", ""),
        {f"{PG}::test_editing_a_page_still_advances_updated_at"},
    ),
    (
        "delete-page-touches-article-updated-at",
        "解绑时不再显式写 updated_at：删一个页面刷一整批文章的时间戳",
        PAGES_API,
        sub(", Article.updated_at: Article.updated_at}", "}"),
        {f"{PG}::test_delete_page_unbinds_articles_without_touching_updated_at"},
    ),
    (
        "delete-page-doesnt-unbind",
        "解绑整句没了，文章仍指着已删掉的页面",
        PAGES_API,
        sub(FIXED_UNBIND, ""),
        {f"{PG}::test_delete_page_unbinds_articles_without_touching_updated_at"},
    ),
]


def run_tests() -> set[str]:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *TEST_FILES,
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
        f"{m.group(1)}::{m.group(2)}"
        for line in proc.stdout.splitlines()
        if (m := re.match(r"FAILED tests/(\S+?)::([A-Za-z0-9_]+)", line))
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
            print(f"{name:<38} {path.name:<12} {why}")
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
