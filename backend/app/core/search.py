"""全站搜索：SQLite FTS5（trigram 分词，兼顾中英文子串匹配）+ LIKE 兜底。

设计要点：
- FTS5 用「外部内容表」(content='articles')，索引不额外存正文，靠触发器与
  articles 表保持同步；启动时 rebuild 一次，兜住「引入 FTS 前已存在的文章」。
- trigram 分词要求匹配词 >=3 字符：故 <3 字符的查询（如 2 字中文词、"AI"）走
  LIKE 兜底；FTS 不可用（旧 SQLite 无 trigram / 测试内存库未建 FTS）时也自动兜底。
- 高亮在后端生成「安全 HTML」：先 HTML 转义正文，再把关键词包成 <mark>，前端可直接
  v-html，无 XSS 面（正文本就是管理员本人撰写，双保险）。
"""
import html
import re

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.article import Article

# FTS5 虚拟表 + 触发器 DDL（均 IF NOT EXISTS，可反复执行）
_CREATE_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS article_fts USING fts5(
    title, summary, content,
    content='articles',
    content_rowid='id',
    tokenize='trigram'
)
"""

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


def ensure_article_fts(engine) -> None:
    """建 FTS5 虚拟表 + 同步触发器，并 rebuild 一次。

    幂等；若运行环境的 SQLite 不支持 fts5/trigram（或非 SQLite），静默跳过，
    搜索届时自动退化为 LIKE。
    """
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(_CREATE_TABLE)
            for ddl in _CREATE_TRIGGERS:
                conn.exec_driver_sql(ddl)
            conn.exec_driver_sql("INSERT INTO article_fts(article_fts) VALUES('rebuild')")
    except Exception:
        # 环境不支持则跳过；不影响应用启动
        pass


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


def _excerpt_html(content: str, summary: str, terms: list[str], width: int = 150) -> str:
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


# ---------- 取匹配文章 id（FTS 优先，LIKE 兜底）----------


def _fts_query(db: Session, match: str, offset: int, size: int) -> tuple[list[int], int]:
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
            # 列权重：标题 > 摘要 > 正文（bm25 越小越相关，升序）
            "ORDER BY bm25(article_fts, 10.0, 4.0, 1.0) "
            "LIMIT :lim OFFSET :off"
        ),
        {"m": match, "lim": size, "off": offset},
    ).all()
    return [r[0] for r in rows], total


def _like_escape(t: str) -> str:
    t = t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{t}%"


def _like_query(db: Session, terms: list[str], offset: int, size: int) -> tuple[list[int], int]:
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


def search_published(db: Session, q: str, page: int, size: int) -> dict:
    """搜索已发布文章，返回统一分页形状（items 含高亮 title_html/excerpt_html）。"""
    q = (q or "").strip()
    terms = [t for t in q.split() if t]
    if not terms:
        return {"total": 0, "page": page, "size": size, "query": q, "items": []}

    offset = (page - 1) * size
    fts_terms = [t for t in terms if len(t) >= 3]

    if fts_terms:
        match = " AND ".join('"%s"' % t.replace('"', '""') for t in fts_terms)
        try:
            ids, total = _fts_query(db, match, offset, size)
        except OperationalError:
            db.rollback()  # FTS 不可用：复位会话后走 LIKE
            ids, total = _like_query(db, terms, offset, size)
    else:
        ids, total = _like_query(db, terms, offset, size)

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
