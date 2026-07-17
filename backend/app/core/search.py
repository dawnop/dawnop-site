"""全站搜索：SQLite FTS5 + LIKE 兜底。

分词器优先级（启动时自动择优，见 ensure_article_fts）：
- **simple**（wangfenjin/simple 扩展，若已加载）：字级 + 拼音，2 字中文词也能进
  bm25 排序、支持拼音/首字母检索。查询直接交给 `simple_query()` 组装 MATCH 表达式。
  只用 simple_query（不启用 jieba），几乎零内存开销。
- **trigram**（内置）：扩展不可用时退回；子串匹配，<3 字符查询走 LIKE。
- 都没有（旧 SQLite / 测试内存库未建 FTS）：整体退化为 LIKE。

其余设计：
- FTS5 用「外部内容表」(content='articles')，靠触发器与 articles 同步；启动 rebuild 一次。
- 高亮在后端生成「安全 HTML」：先 HTML 转义正文，再把关键词包成 <mark>，前端可直接
  v-html（正文本就是管理员本人撰写，双保险）。拼音查询不高亮中文（已知的小取舍）。
"""

import html
import re

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.article import Article

_TRIGGER_NAMES = ("articles_fts_ai", "articles_fts_ad", "articles_fts_au")

_CREATE_TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS articles_fts_ai AFTER INSERT ON articles BEGIN
        INSERT INTO article_fts(rowid, title, summary, content)
        VALUES (new.id, new.title, coalesce(new.summary, ''), coalesce(new.content, ''));
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS articles_fts_ad AFTER DELETE ON articles BEGIN
        INSERT INTO article_fts(article_fts, rowid, title, summary, content)
        VALUES ('delete', old.id, coalesce(old.title, ''), coalesce(old.summary, ''), coalesce(old.content, ''));
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS articles_fts_au AFTER UPDATE ON articles BEGIN
        INSERT INTO article_fts(article_fts, rowid, title, summary, content)
        VALUES ('delete', old.id, coalesce(old.title, ''), coalesce(old.summary, ''), coalesce(old.content, ''));
        INSERT INTO article_fts(rowid, title, summary, content)
        VALUES (new.id, new.title, coalesce(new.summary, ''), coalesce(new.content, ''));
    END
    """,
]


def _simple_available(conn) -> bool:
    """simple 扩展是否已加载（能否调用 simple_query）。"""
    try:
        conn.exec_driver_sql("SELECT simple_query('x')")
        return True
    except Exception:
        return False


def ensure_article_fts(engine) -> None:
    """建 FTS5 虚拟表 + 同步触发器并 rebuild；分词器自动择优（simple > trigram）。

    幂等：已存在但分词器与本次选定不一致时，先 drop 再按新分词器重建（索引由 articles
    表 rebuild 而来，drop 无损）。运行环境完全不支持 FTS 时静默跳过 → 搜索退化为 LIKE。
    """
    try:
        with engine.begin() as conn:
            desired = "simple" if _simple_available(conn) else "trigram"
            row = conn.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE name = 'article_fts'"
            ).fetchone()
            if row and row[0] and f"'{desired}'" not in row[0]:
                for trig in _TRIGGER_NAMES:
                    conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trig}")
                conn.exec_driver_sql("DROP TABLE IF EXISTS article_fts")
            conn.exec_driver_sql(
                "CREATE VIRTUAL TABLE IF NOT EXISTS article_fts USING fts5("
                "title, summary, content, content='articles', content_rowid='id', "
                f"tokenize='{desired}')"
            )
            for ddl in _CREATE_TRIGGERS:
                conn.exec_driver_sql(ddl)
            conn.exec_driver_sql(
                "INSERT INTO article_fts(article_fts) VALUES('rebuild')"
            )
    except Exception:
        pass


def _fts_tokenizer(db: Session) -> str | None:
    """探测当前库里 article_fts 用的分词器（无表则 None）。每次查询一次，开销可忽略。"""
    row = db.execute(
        text("SELECT sql FROM sqlite_master WHERE name = 'article_fts'")
    ).fetchone()
    if not row or not row[0]:
        return None
    return "simple" if "'simple'" in row[0] else "trigram"


# ---------- 关键词高亮（生成安全 HTML）----------

_MD_FENCE = re.compile(r"```.*?```", re.S)
_MD_INLINE = re.compile(r"`[^`]*`")
_MD_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_SYM = re.compile(r"[#>*_~`]+")
_WS = re.compile(r"\s+")


def _strip_md(s: str) -> str:
    """把 Markdown 粗略转成纯文本，供摘要窗口截取（够用即可，不求精确）。"""
    s = _MD_FENCE.sub(" ", s)
    s = _MD_INLINE.sub(" ", s)
    s = _MD_IMG.sub(" ", s)
    s = _MD_LINK.sub(r"\1", s)
    s = _MD_SYM.sub(" ", s)
    return _WS.sub(" ", s).strip()


def _mark(escaped: str, terms: list[str]) -> str:
    """在「已转义」文本里把关键词包成 <mark>。一次性正则替换，避免把注入的标签再匹配。"""
    if not terms:
        return escaped
    pat = "|".join(re.escape(html.escape(t)) for t in terms if t)
    if not pat:
        return escaped
    return re.sub(f"({pat})", r"<mark>\1</mark>", escaped, flags=re.IGNORECASE)


def _title_html(title: str, terms: list[str]) -> str:
    return _mark(html.escape(title), terms)


def _excerpt_html(
    content: str, summary: str, terms: list[str], width: int = 150
) -> str:
    """取一段含关键词的正文窗口作为摘要，命中处高亮；找不到关键词则取开头。"""
    base = _strip_md(content or "") or (summary or "")
    if not base:
        return ""
    low = base.lower()
    pos = -1
    for t in terms:
        i = low.find(t.lower())
        if i != -1 and (pos == -1 or i < pos):
            pos = i
    if pos == -1:
        seg = base[:width]
        pre, suf = "", ("…" if len(base) > width else "")
    else:
        start = max(0, pos - width // 3)
        end = min(len(base), start + width)
        seg = base[start:end]
        pre = "…" if start > 0 else ""
        suf = "…" if end < len(base) else ""
    return pre + _mark(html.escape(seg), terms) + suf


# ---------- 取匹配文章 id（simple / trigram FTS 优先，LIKE 兜底）----------


def _simple_query(db: Session, q: str, offset: int, size: int) -> tuple[list[int], int]:
    total = (
        db.execute(
            text(
                "SELECT count(*) FROM article_fts "
                "JOIN articles a ON a.id = article_fts.rowid "
                "WHERE article_fts MATCH simple_query(:q) AND a.published = 1"
            ),
            {"q": q},
        ).scalar()
        or 0
    )
    rows = db.execute(
        text(
            "SELECT a.id FROM article_fts "
            "JOIN articles a ON a.id = article_fts.rowid "
            "WHERE article_fts MATCH simple_query(:q) AND a.published = 1 "
            "ORDER BY bm25(article_fts, 10.0, 4.0, 1.0) "
            "LIMIT :lim OFFSET :off"
        ),
        {"q": q, "lim": size, "off": offset},
    ).all()
    return [r[0] for r in rows], total


def _trigram_query(
    db: Session, match: str, offset: int, size: int
) -> tuple[list[int], int]:
    total = (
        db.execute(
            text(
                "SELECT count(*) FROM article_fts "
                "JOIN articles a ON a.id = article_fts.rowid "
                "WHERE article_fts MATCH :m AND a.published = 1"
            ),
            {"m": match},
        ).scalar()
        or 0
    )
    rows = db.execute(
        text(
            "SELECT a.id FROM article_fts "
            "JOIN articles a ON a.id = article_fts.rowid "
            "WHERE article_fts MATCH :m AND a.published = 1 "
            "ORDER BY bm25(article_fts, 10.0, 4.0, 1.0) "
            "LIMIT :lim OFFSET :off"
        ),
        {"m": match, "lim": size, "off": offset},
    ).all()
    return [r[0] for r in rows], total


def _like_escape(t: str) -> str:
    t = t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{t}%"


def _like_query(
    db: Session, terms: list[str], offset: int, size: int
) -> tuple[list[int], int]:
    clauses, params = [], {}
    for i, t in enumerate(terms):
        params[f"t{i}"] = _like_escape(t)
        clauses.append(
            f"(title LIKE :t{i} ESCAPE '\\' OR summary LIKE :t{i} ESCAPE '\\' "
            f"OR content LIKE :t{i} ESCAPE '\\')"
        )
    where = " AND ".join(clauses)
    total = (
        db.execute(
            text(f"SELECT count(*) FROM articles WHERE published = 1 AND {where}"),
            params,
        ).scalar()
        or 0
    )
    rows = db.execute(
        text(
            f"SELECT id FROM articles WHERE published = 1 AND {where} "
            "ORDER BY created_at DESC LIMIT :lim OFFSET :off"
        ),
        {**params, "lim": size, "off": offset},
    ).all()
    return [r[0] for r in rows], total


def _collect_ids(db: Session, q: str, terms: list[str], offset: int, size: int):
    """按当前分词器取匹配文章 id；FTS 出错时复位会话并退回 LIKE。"""
    tok = _fts_tokenizer(db)
    if tok == "simple":
        try:
            return _simple_query(db, q, offset, size)
        except OperationalError:
            db.rollback()
            return _like_query(db, terms, offset, size)
    if tok == "trigram":
        fts_terms = [t for t in terms if len(t) >= 3]
        if fts_terms:
            match = " AND ".join('"%s"' % t.replace('"', '""') for t in fts_terms)
            try:
                return _trigram_query(db, match, offset, size)
            except OperationalError:
                db.rollback()
                return _like_query(db, terms, offset, size)
        return _like_query(db, terms, offset, size)
    return _like_query(db, terms, offset, size)


def search_published(db: Session, q: str, page: int, size: int) -> dict:
    """搜索已发布文章，返回统一分页形状（items 含高亮 title_html/excerpt_html）。"""
    q = (q or "").strip()
    terms = [t for t in q.split() if t]
    if not terms:
        return {"total": 0, "page": page, "size": size, "query": q, "items": []}

    offset = (page - 1) * size
    ids, total = _collect_ids(db, q, terms, offset, size)

    if not ids:
        return {"total": total, "page": page, "size": size, "query": q, "items": []}

    by_id = {a.id: a for a in db.query(Article).filter(Article.id.in_(ids)).all()}
    items = []
    for aid in ids:  # 保持相关性/时间排序
        a = by_id.get(aid)
        if a is None:
            continue
        items.append(
            {
                "id": a.id,
                "title": a.title,
                "slug": a.slug,
                "created_at": a.created_at,
                "word_count": a.word_count,
                "tags": a.tags,
                "title_html": _title_html(a.title, terms),
                "excerpt_html": _excerpt_html(a.content, a.summary, terms),
            }
        )
    return {"total": total, "page": page, "size": size, "query": q, "items": items}
