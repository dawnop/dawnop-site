"""浏览计数不得改写 updated_at。

生产事故（回滚演练）：流量切回 FastAPI 的几十秒里，凡被匿名访问过的已发布文章，
`updated_at` 全被刷成了当时那两秒——因为 `views` 自增走的是 ORM 属性赋值，
`Article.updated_at` 上的 `onupdate=func.now()` 对该表的**任何** UPDATE 都生效。
Dawn 侧的 `bump_views` 写的是 `updated_at = updated_at`，从来没有这个问题。

夹具里的文章一律种一个**很旧**的 updated_at：SQLite 的 CURRENT_TIMESTAMP 只到秒，
若用「刚创建」的时间戳，被改写和没被改写在同一秒内长得一模一样，判词等于没写。
比较的是库里存的原始字符串（逐字节），不是解析后的 datetime。
"""

from datetime import datetime

from app.models.article import Article
from sqlalchemy import text

OLD = datetime(2020, 1, 2, 3, 4, 5)


def _seed(db_session, **over):
    """直接入库建文章（INSERT 不触发 onupdate），返回 (id, slug)。"""
    fields = {
        "title": "Counted",
        "slug": "counted",
        "summary": "",
        "content": "body",
        "published": True,
        "views": 0,
        "created_at": OLD,
        "updated_at": OLD,
    }
    fields.update(over)
    session = db_session()
    article = Article(**fields)
    session.add(article)
    session.commit()
    ident = (article.id, article.slug)
    session.close()
    return ident


def _stored_updated_at(db_session, article_id):
    """读库里存着的原始值（SQLite 里就是一串文本），供逐字节比对。"""
    session = db_session()
    try:
        return session.execute(
            text("SELECT updated_at FROM articles WHERE id = :id"),
            {"id": article_id},
        ).scalar_one()
    finally:
        session.close()


def test_anonymous_read_bumps_views_without_touching_updated_at(client, db_session):
    article_id, slug = _seed(db_session)
    before = _stored_updated_at(db_session, article_id)

    body = client.get(f"/api/articles/{slug}").json()

    assert body["views"] == 1
    assert _stored_updated_at(db_session, article_id) == before
    assert datetime.fromisoformat(body["updated_at"]) == OLD


def test_repeated_reads_never_touch_updated_at(client, db_session):
    # 防「只有第一次不变」这种半修
    article_id, slug = _seed(db_session)
    before = _stored_updated_at(db_session, article_id)

    for expected in (1, 2, 3):
        body = client.get(f"/api/articles/{slug}").json()
        assert body["views"] == expected
        assert _stored_updated_at(db_session, article_id) == before


def test_admin_preview_of_draft_changes_nothing(client, auth_headers, db_session):
    article_id, slug = _seed(db_session, published=False, slug="draft")
    before = _stored_updated_at(db_session, article_id)

    body = client.get(f"/api/articles/{slug}", headers=auth_headers).json()

    assert body["views"] == 0
    assert _stored_updated_at(db_session, article_id) == before


def test_admin_preview_of_published_changes_nothing(client, auth_headers, db_session):
    article_id, slug = _seed(db_session)
    before = _stored_updated_at(db_session, article_id)

    body = client.get(f"/api/articles/{slug}", headers=auth_headers).json()

    assert body["views"] == 0
    assert _stored_updated_at(db_session, article_id) == before


def test_real_edit_still_advances_updated_at(client, auth_headers, db_session):
    # 这条守的是修法本身：把 onupdate 整个删掉也能让上面几条变绿，但那不是修，是拆表。
    article_id, slug = _seed(db_session)
    before = _stored_updated_at(db_session, article_id)

    body = client.put(
        f"/api/articles/{article_id}",
        json={"content": "edited"},
        headers=auth_headers,
    ).json()

    assert _stored_updated_at(db_session, article_id) != before
    assert datetime.fromisoformat(body["updated_at"]) > OLD
    assert client.get(f"/api/articles/{slug}").status_code == 200
