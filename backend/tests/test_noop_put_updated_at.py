"""#266：PUT 没有真正改变任何字段值时，updated_at 不变。

同一个家族的第四处（前三处见 test_page_updated_at.py / test_view_counter.py），
但这次出格的是 Dawn：它在 PUT 的末尾无条件 `update ... set updated_at = datetime('now')`，
于是「打开再原样保存一次」会把那一行顶到后台列表最前面（viz 列表按 updated_at desc 排）。
FastAPI 这边不会，因为 SQLAlchemy flush 时逐列比对，一列都没动就整条 UPDATE 都不发，
onupdate 自然不触发。裁决是修 Dawn 往这边对齐。

于是这个文件钉的不是「刚修好的行为」，而是**参照系本身**：这里的通过是 Dawn 侧
`db/sql.row_sig` 那套 before/after 快照要对齐的目标。它是涌现出来的（没有一行代码
写着「没变就别发 UPDATE」），所以特别容易在重构里被悄悄改掉——比如某天把
`setattr` 换成 `db.query(...).update({...})`，那是无条件 UPDATE，contract 当场断，
而在此之前没有任何判词会红。

同 test_page_updated_at.py：种很旧的时间戳，因为 SQLite 的 CURRENT_TIMESTAMP 只到秒，
用「刚创建」的时间戳会让被刷写和没被刷写长得一模一样。比对的是库里存的原始字符串。
"""

from datetime import datetime

import pytest
from app.models.article import Article
from app.models.page import Page
from app.models.viz import VizComponent
from sqlalchemy import text

OLD = datetime(2020, 1, 2, 3, 4, 5)


def _stored(db_session, table, row_id):
    """库里存着的 updated_at 原始值，供逐字节比对。"""
    session = db_session()
    try:
        return session.execute(
            text(f"SELECT updated_at FROM {table} WHERE id = :i"), {"i": row_id}
        ).scalar()
    finally:
        session.close()


def _seed(db_session, obj):
    """直接入库（INSERT 不触发 onupdate），返回新行 id。"""
    session = db_session()
    session.add(obj)
    session.commit()
    row_id = obj.id
    session.close()
    return row_id


# (名字, 建行, 路由前缀, 表名, 原样重存的整份 body, 改一个字段的 body)
CASES = [
    (
        "article",
        lambda: Article(
            title="T",
            slug="t",
            summary="S",
            content="B",
            published=True,
            auto_title=False,
            views=0,
            created_at=OLD,
            updated_at=OLD,
        ),
        "/api/articles",
        "articles",
        {
            "title": "T",
            "slug": "t",
            "summary": "S",
            "content": "B",
            "published": True,
            "auto_title": False,
            # 空标签集合原样重存也不算改（有标签的情形见下面 tag 专项）
            "tags": [],
        },
        {"summary": "S2"},
    ),
    (
        "page",
        lambda: Page(
            title="P",
            slug="p",
            type="content",
            description="D",
            content="C",
            auto_title=False,
            nav_visible=True,
            nav_order=3,
            created_at=OLD,
            updated_at=OLD,
        ),
        "/api/pages",
        "pages",
        {
            "title": "P",
            "slug": "p",
            "type": "content",
            "description": "D",
            "content": "C",
            "auto_title": False,
            "nav_visible": True,
            "nav_order": 3,
        },
        {"description": "D2"},
    ),
    (
        "viz",
        lambda: VizComponent(
            slug="v",
            name="N",
            source="SRC",
            compiled="CMP",
            style="STY",
            created_at=OLD,
            updated_at=OLD,
        ),
        "/api/viz",
        "viz_components",
        {
            "slug": "v",
            "name": "N",
            "source": "SRC",
            "compiled": "CMP",
            "style": "STY",
        },
        {"name": "N2"},
    ),
]

IDS = [c[0] for c in CASES]


@pytest.mark.parametrize("name,make,prefix,table,same,changed", CASES, ids=IDS)
def test_resaving_the_same_values_is_not_an_edit(
    client, auth_headers, db_session, name, make, prefix, table, same, changed
):
    row_id = _seed(db_session, make())
    before = _stored(db_session, table, row_id)

    resp = client.put(f"{prefix}/{row_id}", json=same, headers=auth_headers)
    assert resp.status_code == 200
    # 整份字段都传了，但每一个值都和库里一模一样，所以一条 UPDATE 都没发
    assert _stored(db_session, table, row_id) == before


@pytest.mark.parametrize("name,make,prefix,table,same,changed", CASES, ids=IDS)
def test_empty_body_is_not_an_edit(
    client, auth_headers, db_session, name, make, prefix, table, same, changed
):
    row_id = _seed(db_session, make())
    before = _stored(db_session, table, row_id)

    resp = client.put(f"{prefix}/{row_id}", json={}, headers=auth_headers)
    assert resp.status_code == 200
    assert _stored(db_session, table, row_id) == before


@pytest.mark.parametrize("name,make,prefix,table,same,changed", CASES, ids=IDS)
def test_changing_one_field_still_advances_updated_at(
    client, auth_headers, db_session, name, make, prefix, table, same, changed
):
    """反方向：守住「没变就不推进」不能顺手把「变了也不推进」一起守进去。"""
    row_id = _seed(db_session, make())
    before = _stored(db_session, table, row_id)

    resp = client.put(f"{prefix}/{row_id}", json=changed, headers=auth_headers)
    assert resp.status_code == 200
    assert _stored(db_session, table, row_id) != before
    assert datetime.fromisoformat(resp.json()["updated_at"]) > OLD


def _seed_tagged_article(db_session):
    return _seed(
        db_session,
        Article(
            title="T",
            slug="t",
            summary="S",
            content="B",
            published=True,
            views=0,
            created_at=OLD,
            updated_at=OLD,
        ),
    )


def _restale(db_session, row_id):
    """把 updated_at 按回旧值，好让同一篇文章连着测好几次。"""
    session = db_session()
    session.execute(
        text("UPDATE articles SET updated_at = :t WHERE id = :i"),
        {"t": OLD, "i": row_id},
    )
    session.commit()
    session.close()


def test_retagging_advances_updated_at(client, auth_headers, db_session):
    """改标签是编辑，只不过落在关联表上。

    SQLAlchemy 只写 article_tags、不对 articles 发 UPDATE，onupdate 不触发，
    所以 app/api/articles.py 显式推进。这条判词钉的就是那句显式推进（#266）。
    """
    row_id = _seed_tagged_article(db_session)
    before = _stored(db_session, "articles", row_id)

    resp = client.put(
        f"/api/articles/{row_id}", json={"tags": ["jvm"]}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert [t["name"] for t in resp.json()["tags"]] == ["jvm"]
    assert _stored(db_session, "articles", row_id) != before


def test_reordering_the_same_tags_is_not_an_edit(client, auth_headers, db_session):
    """标签是集合：同样两个名字换个顺序，集合没变，不推进。

    第二次故意传 ["jvm", "dawn"]——不是 Tag.name 的排序。`article.tags` 关系带
    order_by=Tag.name，所以按**列表**比会判成变了、按集合比才不变；顺序这么挑，
    「拿列表比」的变异体才会被这条判词照出来。
    """
    row_id = _seed_tagged_article(db_session)
    resp = client.put(
        f"/api/articles/{row_id}", json={"tags": ["dawn", "jvm"]}, headers=auth_headers
    )
    assert resp.status_code == 200

    _restale(db_session, row_id)
    before = _stored(db_session, "articles", row_id)
    resp = client.put(
        f"/api/articles/{row_id}", json={"tags": ["jvm", "dawn"]}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert {t["name"] for t in resp.json()["tags"]} == {"jvm", "dawn"}
    assert _stored(db_session, "articles", row_id) == before


def test_dropping_or_clearing_tags_advances_updated_at(
    client, auth_headers, db_session
):
    row_id = _seed_tagged_article(db_session)
    client.put(
        f"/api/articles/{row_id}", json={"tags": ["jvm", "dawn"]}, headers=auth_headers
    )

    _restale(db_session, row_id)
    before = _stored(db_session, "articles", row_id)
    resp = client.put(
        f"/api/articles/{row_id}", json={"tags": ["jvm"]}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert _stored(db_session, "articles", row_id) != before

    _restale(db_session, row_id)
    before = _stored(db_session, "articles", row_id)
    resp = client.put(
        f"/api/articles/{row_id}", json={"tags": []}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["tags"] == []
    assert _stored(db_session, "articles", row_id) != before
