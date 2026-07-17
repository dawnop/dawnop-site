"""搜索接口测试：FTS 路径（带 fts 夹具建虚拟表）+ LIKE 兜底路径。"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import search as search_mod
from app.core.search import ensure_article_fts, search_published
from app.database import Base
from app.models.article import Article

_EXT = Path(__file__).resolve().parents[1] / "extensions" / "libsimple"


def _ext_present() -> bool:
    return any(_EXT.with_suffix(s).exists() for s in (".so", ".dylib", ".dll"))


@pytest.fixture
def fts(db_session):
    """在测试引擎上建好 FTS5 表 + 触发器；建在造数据之前，后续文章由触发器入索引。"""
    engine = db_session.kw["bind"]
    search_mod.ensure_article_fts(engine)
    yield


def _create(client, headers, **kw):
    payload = {
        "title": "t",
        "content": "",
        "summary": "",
        "published": True,
        "tags": [],
    }
    payload.update(kw)
    r = client.post("/api/articles", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _search(client, q, **params):
    r = client.get("/api/search", params={"q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_empty_query_returns_nothing(client):
    data = _search(client, "   ")
    assert data["total"] == 0
    assert data["items"] == []


def test_fts_match_by_title(client, auth_headers, fts):
    _create(client, auth_headers, title="CUDA 编程指南", content="讲 GPU 并行")
    _create(client, auth_headers, title="无关文章", content="随便写点东西")
    data = _search(client, "CUDA")
    assert data["total"] == 1
    item = data["items"][0]
    assert item["title"] == "CUDA 编程指南"
    assert "<mark>CUDA</mark>" in item["title_html"]


def test_fts_match_in_content_excerpt_highlight(client, auth_headers, fts):
    _create(
        client,
        auth_headers,
        title="性能笔记",
        content="正文里提到 bitonic 排序网络的实现细节。",
    )
    data = _search(client, "bitonic")
    assert data["total"] == 1
    assert "<mark>bitonic</mark>" in data["items"][0]["excerpt_html"]


def test_only_published_are_searchable(client, auth_headers, fts):
    _create(client, auth_headers, title="草稿里的 CUDA", published=False)
    _create(client, auth_headers, title="已发布 CUDA", published=True)
    data = _search(client, "CUDA")
    assert data["total"] == 1
    assert data["items"][0]["title"] == "已发布 CUDA"


def test_title_ranked_above_content(client, auth_headers, fts):
    _create(client, auth_headers, title="仅正文提到关键", content="这里有 bitonic 字样")
    _create(client, auth_headers, title="标题就含 bitonic", content="正文无关")
    data = _search(client, "bitonic")
    assert data["total"] == 2
    # 列权重让标题命中排在前面
    assert data["items"][0]["title"] == "标题就含 bitonic"


def test_multi_term_and_semantics(client, auth_headers, fts):
    _create(client, auth_headers, title="GPU 与 CUDA", content="两者都提到")
    _create(client, auth_headers, title="只有 GPU", content="没有别的")
    data = _search(client, "GPU CUDA")
    assert data["total"] == 1
    assert data["items"][0]["title"] == "GPU 与 CUDA"


def test_pagination(client, auth_headers, fts):
    for i in range(5):
        _create(client, auth_headers, title=f"bitonic 第 {i} 篇")
    p1 = _search(client, "bitonic", page=1, size=2)
    assert p1["total"] == 5
    assert len(p1["items"]) == 2
    p3 = _search(client, "bitonic", page=3, size=2)
    assert len(p3["items"]) == 1


def test_no_results(client, auth_headers, fts):
    _create(client, auth_headers, title="随便一篇")
    data = _search(client, "zzzunlikelyquery")
    assert data["total"] == 0
    assert data["items"] == []


def test_short_query_uses_like_fallback(client, auth_headers, fts):
    # 2 字符查询 trigram 不支持 → 自动走 LIKE，仍应命中
    _create(client, auth_headers, title="谈谈 AI 的发展")
    data = _search(client, "AI")
    assert data["total"] == 1
    assert "<mark>AI</mark>" in data["items"][0]["title_html"]


def test_search_works_without_fts_table(client, auth_headers):
    """未建 FTS 表（默认夹具即如此）时，仍能通过 LIKE 兜底搜索。"""
    _create(client, auth_headers, title="CUDA 与 GPU 编程")
    data = _search(client, "CUDA")
    assert data["total"] == 1
    assert data["items"][0]["title"] == "CUDA 与 GPU 编程"


def test_tags_included_in_results(client, auth_headers, fts):
    _create(client, auth_headers, title="bitonic 排序", tags=["算法", "GPU"])
    data = _search(client, "bitonic")
    names = {t["name"] for t in data["items"][0]["tags"]}
    assert names == {"算法", "GPU"}


@pytest.mark.skipif(
    not _ext_present(), reason="simple 扩展未下载（scripts/fetch_simple_ext.py）"
)
def test_simple_tokenizer_short_cjk_and_pinyin():
    """扩展在位时走 simple 分词：2 字中文词与拼音都应命中（trigram 做不到）。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _load(dbapi_conn, _rec):
        dbapi_conn.enable_load_extension(True)
        dbapi_conn.load_extension(str(_EXT))
        dbapi_conn.enable_load_extension(False)

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    db.add(
        Article(
            title="CUDA 内存模型笔记",
            slug="m",
            content="内存层级与合并访问",
            published=True,
        )
    )
    db.commit()

    ensure_article_fts(engine)  # 应择优选中 simple 并 rebuild
    assert search_mod._fts_tokenizer(db) == "simple"

    assert search_published(db, "内存", 1, 10)["total"] == 1  # 2 字中文
    assert search_published(db, "neicun", 1, 10)["total"] == 1  # 全拼
    assert search_published(db, "nc", 1, 10)["total"] == 1  # 首字母
    db.close()
