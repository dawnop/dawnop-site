#!/usr/bin/env python3
"""Apply one compiling updated_at boundary mutant in place.

The contract under test (#264, #266, #267): only an explicit edit to a row
advances its `updated_at`. A PUT that rewrites every field with the value
already stored changed nothing and must leave the stamp alone; changing any one
of them — tags included, they are that article's tags — must advance it. A
rename or a move rewrites `path` and nothing else, so it leaves every stamp in
the subtree alone; writing a file's bytes still advances it.

Each mutant is a plausible way to get that wrong, and each must still compile:
a mutant that fails to build tests the compiler, not the assertions.
"""

import argparse
from pathlib import Path

ARTICLE_TOUCH = "let _u = if after != before { touch_article(c, id)? } else { 0 }"
PAGE_TOUCH = "let _u = if after != before { touch_page(c, id)? } else { 0 }"
VIZ_TOUCH = "let _t = if after != before { touch_viz(c, id)? } else { 0 }"

ARTICLE_TAG_FOLD = """  let tags = article_tag_sig(c, id)?
  Ok("$cols|tags=$tags")
"""

ARTICLE_COLS = '["title", "slug", "summary", "content", "published", "auto_title", "page_id", "created_at"]'

TAG_ORDERED = "select tag_id from article_tags where article_id = ? order by tag_id"

REPARENT = '"update files set path = ? where path = ?"'

# The upsert behind every file write (proxy upload, /register, WebDAV PUT, text
# save). Its stamp is the one a move must *not* imitate.
FILE_UPSERT_STAMP = (
    ", size = excluded.size, updated_at = datetime('now') where files.is_dir = 0"
)


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected one anchor in {path}, found {count}: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


MUTANTS = {
    # ---- the three guards, both directions ----
    "article-touch-unconditional": (
        "src/repo/repo_write.dawn",
        ARTICLE_TOUCH,
        "let _u = touch_article(c, id)?",
    ),
    "article-never-touches": (
        "src/repo/repo_write.dawn",
        ARTICLE_TOUCH,
        "let _u = 0",
    ),
    "page-touch-unconditional": (
        "src/api/api_pages.dawn",
        PAGE_TOUCH,
        "let _u = touch_page(c, id)?",
    ),
    "page-never-touches": (
        "src/api/api_pages.dawn",
        PAGE_TOUCH,
        "let _u = 0",
    ),
    "viz-touch-unconditional": (
        "src/api/api_viz.dawn",
        VIZ_TOUCH,
        "let _t = touch_viz(c, id)?",
    ),
    "viz-never-touches": (
        "src/api/api_viz.dawn",
        VIZ_TOUCH,
        "let _t = 0",
    ),
    # ---- what the signature is made of ----
    "article-sig-drops-tags": (
        "src/repo/repo_write.dawn",
        ARTICLE_TAG_FOLD,
        "  Ok(cols)\n",
    ),
    "article-tag-sig-unordered": (
        "src/repo/repo_write.dawn",
        TAG_ORDERED,
        "select tag_id from article_tags where article_id = ?",
    ),
    "article-sig-includes-views": (
        "src/repo/repo_write.dawn",
        ARTICLE_COLS,
        ARTICLE_COLS.replace('"created_at"]', '"created_at", "views"]'),
    ),
    # ---- a move is not an edit, both directions ----
    "move-stamps-now": (
        "src/repo/repo_fm.dawn",
        REPARENT,
        "\"update files set path = ?, updated_at = datetime('now') where path = ?\"",
    ),
    "file-write-never-stamps": (
        "src/repo/repo_fm.dawn",
        FILE_UPSERT_STAMP,
        ", size = excluded.size where files.is_dir = 0",
    ),
    # ---- how a signature is spelled ----
    "row-sig-drops-separator": (
        "src/db/sql.dawn",
        'if acc == "" { q } else { "$acc || \'|\' || $q" }',
        'if acc == "" { q } else { "$acc || $q" }',
    ),
    "row-sig-unquoted-values": (
        "src/db/sql.dawn",
        'fn quoted(col: String) -> String = "quote($col)"',
        "fn quoted(col: String) -> String = \"coalesce($col, 'NULL')\"",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mutant", choices=sorted(MUTANTS), nargs="?")
    parser.add_argument("project", type=Path, nargs="?")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        for name in MUTANTS:
            print(name)
        return 0
    if not args.mutant or args.project is None:
        parser.error("need a mutant name and a project directory")

    rel, old, new = MUTANTS[args.mutant]
    replace_once(args.project / rel, old, new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
