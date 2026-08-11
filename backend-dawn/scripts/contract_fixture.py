#!/usr/bin/env python3
"""Deterministic SQLite fixture for the golden contract runs.

The contract scripts used to discover slugs/ids from whatever happened to be in
the live database. That made them un-runnable in CI, and — worse — silently
weak: against a near-empty database `contract_read` "passed" by comparing two
empty lists. Golden mode needs the opposite property: the same bytes out of the
backend on every machine, so a diff means the backend changed.

So the fixture is code, not a committed .db (which .gitignore excludes anyway,
and which nobody could review). Every value below is pinned by hand:

  * ids and timestamps are literals — no autoincrement, no CURRENT_TIMESTAMP,
    so `created_at` ordering and `last_modified` epochs are stable;
  * content is chosen to exercise the shaping code, not just to exist:
    CJK + ASCII word counts, an embedded `<script>` (search escaping), markdown
    fences/links/images (excerpt stripping), an empty body, a draft, an orphan
    tag, an empty directory, non-ASCII paths;
  * there is enough of it to paginate (9 published articles), so `?page=2` is
    not trivially empty.

The schema mirrors `backend/app/models/*` (SQLAlchemy `create_all` output,
dumped once and pinned here). It is duplicated deliberately: the Dawn backend
talks to this schema over JDBC and does not create it, and the FastAPI models
are frozen. If the models ever change, this DDL must change with them — the
golden files will go red, which is the point.

Usage:
    python3 contract_fixture.py /path/to/contract.db
"""

import hashlib
import pathlib
import sqlite3
import sys

# --- credentials -------------------------------------------------------------
# Fixture-only admin. The hash is bcrypt cost 4 (not 12): WebDAV Basic auth
# re-verifies on nearly every request and cost 12 turns a 20-case run into a
# minute of key stretching. jBCrypt on the Dawn side normalizes $2b$ -> $2a$.
FIXTURE_USER = "contract-admin"
FIXTURE_PW = "contract-fixture-pw"
FIXTURE_PW_BCRYPT = "$2b$04$fBm/BBrDqZMMZXAiEDQ8M.VGIhXSwMMKQPmceHoZj3xAZXVtG7YF2"

# --- schema (mirrors backend/app/models, create_all output) -------------------
SCHEMA = """
CREATE TABLE users (
    id INTEGER NOT NULL,
    username VARCHAR(64) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_users_username ON users (username);

CREATE TABLE pages (
    id INTEGER NOT NULL,
    title VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL,
    type VARCHAR(32) NOT NULL,
    description TEXT NOT NULL,
    content TEXT NOT NULL,
    auto_title BOOLEAN DEFAULT 0 NOT NULL,
    nav_visible BOOLEAN NOT NULL,
    nav_order INTEGER NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_pages_slug ON pages (slug);
CREATE INDEX ix_pages_nav_order ON pages (nav_order);

CREATE TABLE articles (
    id INTEGER NOT NULL,
    title VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    published BOOLEAN NOT NULL,
    auto_title BOOLEAN DEFAULT 0 NOT NULL,
    views INTEGER DEFAULT 0 NOT NULL,
    page_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(page_id) REFERENCES pages (id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX ix_articles_slug ON articles (slug);
CREATE INDEX ix_articles_published ON articles (published);
CREATE INDEX ix_articles_page_id ON articles (page_id);

CREATE TABLE tags (
    id INTEGER NOT NULL,
    name VARCHAR(64) NOT NULL,
    slug VARCHAR(64) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_tags_name ON tags (name);
CREATE UNIQUE INDEX ix_tags_slug ON tags (slug);

CREATE TABLE article_tags (
    article_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (article_id, tag_id),
    FOREIGN KEY(article_id) REFERENCES articles (id) ON DELETE CASCADE,
    FOREIGN KEY(tag_id) REFERENCES tags (id) ON DELETE CASCADE
);

CREATE TABLE files (
    id INTEGER NOT NULL,
    path VARCHAR(1024) NOT NULL,
    is_dir BOOLEAN NOT NULL,
    "key" VARCHAR(512),
    content_type VARCHAR(128) NOT NULL,
    size INTEGER NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id)
);
CREATE INDEX ix_files_is_dir ON files (is_dir);
CREATE UNIQUE INDEX ix_files_path ON files (path);

CREATE TABLE pending_uploads (
    "key" VARCHAR(512) NOT NULL,
    path VARCHAR(1024) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY ("key")
);

CREATE TABLE settings (
    "key" VARCHAR(64) NOT NULL,
    value VARCHAR(4096) NOT NULL,
    PRIMARY KEY ("key")
);

CREATE TABLE viz_components (
    id INTEGER NOT NULL,
    slug VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    source TEXT NOT NULL,
    compiled TEXT NOT NULL,
    style TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_viz_components_slug ON viz_components (slug);
"""

# --- rows --------------------------------------------------------------------

T0 = "2026-01-01 00:00:00.000000"


def _ts(day: int, hour: int = 8) -> str:
    return f"2026-01-{day:02d} {hour:02d}:00:00.000000"


# (id, title, slug, type, description, content, auto_title, nav_visible, nav_order)
PAGES = [
    # the two builtin pages app/core/bootstrap.py injects at FastAPI startup
    (1, "首页", "home", "builtin", "", "", 0, 1, -1000),
    (2, "标签", "tags", "builtin", "", "", 0, 0, 1000),
    (3, "博客", "blog", "article_list", "所有文章", "", 0, 1, 10),
    (
        4,
        "关于",
        "about",
        "content",
        "关于本站",
        "# 关于\n\n这是一个 **内容页**，正文里有 `code`、[链接](https://example.invalid)"
        " 和公式 $E = mc^2$。\n",
        0,
        1,
        20,
    ),
    # not in the nav: pins that /api/pages/nav filters on nav_visible
    (5, "手记", "notes", "article_list", "零散记录", "", 0, 0, 30),
]

# (id, name, slug)
TAGS = [
    (1, "dawn", "dawn"),
    (2, "编译器", "bianyiqi"),
    (3, "latex", "latex"),
    # no article uses it: pins that /api/tags drops a tag nobody used while
    # /api/tags/admin still lists it
    (4, "孤儿标签", "orphan-tag"),
]

_ZOO = """# Markdown zoo

正文里放满了 markdown 结构，用来钉住摘要的 strip_md 窗口：

```python
def f(x):
    return x * 100
```

行内代码 `simple_query()`、图片 ![封面](https://example.invalid/c.png)、
链接 [dawn-lang](https://example.invalid/dawn) 和 > 引用。

LaTeX 行内公式 $a^2 + b^2 = c^2$ 也在这里。
"""

_XSS = """Golden tests pin the wire, not the intent.

搜索转义用例的原料就在这一行：<script>alert(1)</script> —— 服务端要把它转义后
再包 <mark>，前端才敢 v-html。

单引号 ' 也留一个，给 SQL 注入用例当靶子。
"""

# (id, title, slug, summary, content, published, auto_title, views, page_id, day)
ARTICLES = [
    (
        1,
        "Dawn 自举：从 Kotlin 到 selfhost",
        "dawn-selfhost",
        "编译器自己编译自己的那一天。",
        "Dawn 的自举分三步走：先用 Kotlin 写出编译器，再用 Dawn 重写一遍，"
        "最后让 Dawn 版编译自己。\n\n每一代产物都要和上一代逐字节对拍。\n",
        1,
        0,
        0,
        3,
        10,
    ),
    (
        2,
        "LaTeX in Markdown",
        "latex-in-markdown",
        "KaTeX renders $x^2$ inline.",
        "LaTeX inside markdown needs a plugin. We use KaTeX, not MathJax:\n\n"
        "$$\\int_0^1 x^2 \\,dx = \\frac{1}{3}$$\n",
        1,
        0,
        0,
        3,
        9,
    ),
    (
        3,
        "后端重写记",
        "backend-rewrite",
        "把 FastAPI 后端换成自制语言。",
        "后端重写不是重写语言，是重写信任。每条路由迁移后都要和旧后端对拍，"
        "直到 100 条用例全绿才敢切流。\n",
        1,
        0,
        0,
        3,
        8,
    ),
    (
        4,
        "Golden tests, a field guide",
        "golden-tests",
        "Record, then diff.",
        _XSS,
        1,
        0,
        0,
        3,
        7,
    ),
    (
        5,
        "Unicode 边界：emoji 🌅 与代理对",
        "unicode-edges",
        "BMP 之外的字符会分叉。",
        "CJK 在 BMP 内不分叉，只有 emoji 🌅 和 astral 平面才会走代理对路径。\n",
        1,
        0,
        0,
        5,
        6,
    ),
    (
        6,
        "Pagination probe",
        "pagination-probe",
        "Page two must not be empty.",
        "Filler.\n",
        1,
        0,
        0,
        3,
        5,
    ),
    (
        7,
        "草稿：不该出现在公开列表",
        "draft-only",
        "这是草稿。",
        "草稿对匿名访客不可见，对搜索也不可见。dawn\n",
        0,
        0,
        0,
        3,
        4,
    ),
    # empty content: word_count 0 and the excerpt falls back to the summary
    (8, "Empty body", "empty-body", "Only a summary here.", "", 1, 0, 0, None, 3),
    (9, "Markdown zoo", "markdown-zoo", "结构大杂烩。", _ZOO, 1, 1, 0, 5, 2),
    (
        10,
        "Zzz last",
        "zzz-last",
        "Sorted last by design.",
        "The tail article.\n",
        1,
        0,
        0,
        None,
        1,
    ),
]

ARTICLE_TAGS = [(1, 1), (1, 2), (2, 3), (3, 1), (5, 2), (7, 1), (9, 1), (9, 3)]

# (id, path, is_dir, key, content_type, size, day)
FILES = [
    (1, "archive", 1, None, "", 0, 2),
    (2, "docs", 1, None, "", 0, 3),
    (3, "docs/img", 1, None, "", 0, 4),
    (
        4,
        "docs/img/cover.png",
        0,
        "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
        "image/png",
        20480,
        5,
    ),
    (
        5,
        "docs/notes.txt",
        0,
        "11111111-2222-3333-4444-555555555555",
        "text/plain",
        137,
        6,
    ),
    (6, "empty-dir", 1, None, "", 0, 7),
    (7, "example.txt", 0, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "text/plain", 42, 8),
    (
        8,
        "readme.md",
        0,
        "99999999-8888-7777-6666-555555555555",
        "text/markdown",
        1024,
        9,
    ),
    (9, "图片", 1, None, "", 0, 10),
    (
        10,
        "图片/风景.jpg",
        0,
        "cafebabe-dead-beef-0123-456789abcdef",
        "image/jpeg",
        65536,
        11,
    ),
    (
        11,
        "legacy-parent.txt",
        0,
        "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "text/plain",
        64,
        12,
    ),
    (
        12,
        "legacy-parent.txt/existing-target.txt",
        0,
        "cccccccc-dddd-eeee-ffff-000000000000",
        "text/plain",
        96,
        13,
    ),
]

# non-default values so GET /api/settings proves the merge, not just DEFAULTS
SETTINGS = [("upload_concurrency", "5"), ("text_preview_max_kb", "256")]

VIZ = [
    (
        1,
        "counter-demo",
        "计数器",
        '<template><button @click="n++">{{ n }}</button></template>\n',
        "const _sfc_main = { data: () => ({ n: 0 }) };\n",
        ".counter { color: red }\n",
    ),
    (
        2,
        "chart-demo",
        "Chart demo",
        '<template><svg width="10" height="10"/></template>\n',
        "const _sfc_main = {};\n",
        "",
    ),
]


def build(db_path: str) -> None:
    """(Re)create the fixture database at db_path."""
    p = pathlib.Path(db_path)
    for suffix in ("", "-wal", "-shm"):
        f = pathlib.Path(str(p) + suffix)
        if f.exists():
            f.unlink()
    p.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)

    con.execute(
        "INSERT INTO users (id, username, password_hash, created_at) VALUES (1, ?, ?, ?)",
        (FIXTURE_USER, FIXTURE_PW_BCRYPT, T0),
    )
    for pid, title, slug, typ, desc, content, auto, vis, order in PAGES:
        con.execute(
            "INSERT INTO pages (id, title, slug, type, description, content, auto_title,"
            " nav_visible, nav_order, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (pid, title, slug, typ, desc, content, auto, vis, order, T0, T0),
        )
    for tid, name, slug in TAGS:
        con.execute(
            "INSERT INTO tags (id, name, slug, created_at) VALUES (?,?,?,?)",
            (tid, name, slug, T0),
        )
    for aid, title, slug, summary, content, pub, auto, views, page_id, day in ARTICLES:
        con.execute(
            "INSERT INTO articles (id, title, slug, summary, content, published, auto_title,"
            " views, page_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                aid,
                title,
                slug,
                summary,
                content,
                pub,
                auto,
                views,
                page_id,
                _ts(day),
                _ts(day),
            ),
        )
    con.executemany(
        "INSERT INTO article_tags (article_id, tag_id) VALUES (?,?)", ARTICLE_TAGS
    )
    for fid, path, is_dir, key, ctype, size, day in FILES:
        con.execute(
            'INSERT INTO files (id, path, is_dir, "key", content_type, size, created_at,'
            " updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (fid, path, is_dir, key, ctype, size, _ts(day, 12), _ts(day, 12)),
        )
    con.executemany('INSERT INTO settings ("key", value) VALUES (?,?)', SETTINGS)
    for vid, slug, name, source, compiled, style in VIZ:
        con.execute(
            "INSERT INTO viz_components (id, slug, name, source, compiled, style,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (vid, slug, name, source, compiled, style, T0, T0),
        )

    _build_fts(con)
    con.commit()
    con.close()


def _build_fts(con: sqlite3.Connection) -> None:
    """Create article_fts the way core/search.py would in this environment.

    core/search.py picks `simple` when the extension is loaded and `trigram`
    otherwise. This seeder never loads the extension, so it creates the trigram
    table — which is also what pins the Dawn search path: Dawn only ever calls
    `simple_query()`, that function does not exist here, and its own
    `catch_fault` fallback drops search to LIKE. Deterministic on every machine
    (see contract_golden.SEARCH_BACKEND for the honesty note that goes with it).
    """
    try:
        con.executescript(
            "CREATE VIRTUAL TABLE article_fts USING fts5("
            "  title, summary, content, content='articles', content_rowid='id',"
            "  tokenize='trigram');"
            "INSERT INTO article_fts(article_fts) VALUES('rebuild');"
        )
    except (
        sqlite3.OperationalError
    ) as e:  # fts5/trigram missing: search still works via LIKE
        print(
            f"note: article_fts not created ({e}); Dawn search uses the LIKE path either way"
        )


def fingerprint() -> str:
    """Short hash of the fixture *data*, recorded into the golden files.

    Editing the fixture without re-recording changes the expected bytes; the
    golden run then fails on the fingerprint with a clear message instead of
    dumping a hundred body diffs. Hashing the data rather than the file keeps a
    comment or docstring edit from invalidating every golden.
    """
    blob = repr(
        (
            SCHEMA,
            PAGES,
            TAGS,
            ARTICLES,
            ARTICLE_TAGS,
            FILES,
            SETTINGS,
            VIZ,
            FIXTURE_USER,
        )
    ).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    build(sys.argv[1])
    print(f"fixture written: {sys.argv[1]}  (fingerprint {fingerprint()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
