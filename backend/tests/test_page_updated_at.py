"""#264：只有对该行的显式编辑才推进 updated_at。

由别处的操作顺带引起的字段变更不推进——「重排导航顺序」改的是 `Page.nav_order`、
「删页面」把旗下文章的 `page_id` 置空，两者都不是对那一行的编辑。两个后端在同一件事
上给出不同答案本身就是缺陷（回滚会在两者之间来回切），这里往 Dawn 对齐：
`repo_pagetag.reorder_pages` 与 `repo_pagetag.delete_page` 的 SQL 都不碰 updated_at。

同 test_view_counter.py：种很旧的时间戳，因为 SQLite 的 CURRENT_TIMESTAMP 只到秒，
用「刚创建」的时间戳会让被刷写和没被刷写长得一模一样。比对的是库里存的原始字符串。

「PUT /api/articles 仍推进 Article.updated_at」那条不在这里重复写，
test_view_counter.py::test_real_edit_still_advances_updated_at 已经守着它，
删 Article 模型 onupdate 的变异体照样被它杀掉（见 scripts/updated_at_mutants.py）。
"""

from datetime import datetime

from app.models.article import Article
from app.models.page import Page
from sqlalchemy import text

OLD = datetime(2020, 1, 2, 3, 4, 5)


def _seed_pages(db_session, count):
    """直接入库建页面（INSERT 不触发 onupdate），返回 id 列表。"""
    session = db_session()
    pages = [
        Page(
            title=f"P{i}",
            slug=f"p{i}",
            type="article_list",
            description="",
            content="",
            nav_visible=True,
            nav_order=i,
            created_at=OLD,
            updated_at=OLD,
        )
        for i in range(count)
    ]
    session.add_all(pages)
    session.commit()
    ids = [p.id for p in pages]
    session.close()
    return ids


def _seed_articles(db_session, page_id, count):
    session = db_session()
    articles = [
        Article(
            title=f"A{i}",
            slug=f"a{i}",
            summary="",
            content="body",
            published=True,
            views=0,
            page_id=page_id,
            created_at=OLD,
            updated_at=OLD,
        )
        for i in range(count)
    ]
    session.add_all(articles)
    session.commit()
    ids = [a.id for a in articles]
    session.close()
    return ids


def _stored(db_session, table, columns, ids):
    """读库里存着的原始值，供逐字节比对。返回 {id: (col, ...)}。"""
    session = db_session()
    try:
        rows = session.execute(
            text(f"SELECT id, {', '.join(columns)} FROM {table}")
        ).all()
    finally:
        session.close()
    return {r[0]: tuple(r[1:]) for r in rows if r[0] in ids}


def test_reorder_does_not_touch_page_updated_at(client, auth_headers, db_session):
    ids = _seed_pages(db_session, 3)
    before = _stored(db_session, "pages", ["updated_at"], ids)

    new_order = [ids[2], ids[0], ids[1]]
    resp = client.post(
        "/api/pages/reorder", json={"ids": new_order}, headers=auth_headers
    )

    assert resp.status_code == 200
    # 顺序真的被改了（判词不能只盯着时间戳）
    assert [p["id"] for p in resp.json() if p["id"] in ids] == new_order
    assert _stored(db_session, "pages", ["nav_order"], ids) == {
        ids[2]: (0,),
        ids[0]: (1,),
        ids[1]: (2,),
    }
    assert _stored(db_session, "pages", ["updated_at"], ids) == before


def test_delete_page_unbinds_articles_without_touching_updated_at(
    client, auth_headers, db_session
):
    page_id = _seed_pages(db_session, 1)[0]
    article_ids = _seed_articles(db_session, page_id, 3)
    before = _stored(db_session, "articles", ["updated_at"], article_ids)

    resp = client.delete(f"/api/pages/{page_id}", headers=auth_headers)

    assert resp.status_code == 204
    after = _stored(db_session, "articles", ["page_id", "updated_at"], article_ids)
    # 每一篇都解绑了，且每一篇的 updated_at 都逐字节没动
    assert len(after) == 3
    for article_id, (page_ref, updated_at) in after.items():
        assert page_ref is None
        assert (updated_at,) == before[article_id]


def test_editing_a_page_still_advances_updated_at(client, auth_headers, db_session):
    # 防止有人把 Page 上的 onupdate 整个删掉来「修」重排那条
    page_id = _seed_pages(db_session, 1)[0]
    before = _stored(db_session, "pages", ["updated_at"], [page_id])

    body = client.put(
        f"/api/pages/{page_id}", json={"content": "edited"}, headers=auth_headers
    ).json()

    assert _stored(db_session, "pages", ["updated_at"], [page_id]) != before
    assert datetime.fromisoformat(body["updated_at"]) > OLD
