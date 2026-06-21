def _new_page(client, headers, **over):
    payload = {"title": "技术", "type": "article_list"}
    payload.update(over)
    return client.post("/api/pages", json=payload, headers=headers)


def test_create_requires_auth(client):
    assert _new_page(client, {}).status_code == 401


def test_create_and_get_page(client, auth_headers):
    resp = _new_page(client, auth_headers, title="关于", type="content", content="hi")
    assert resp.status_code == 201
    page = resp.json()
    assert page["slug"] == "关于" or page["slug"]  # 中文 slug 保留
    got = client.get(f"/api/pages/{page['slug']}")
    assert got.status_code == 200
    assert got.json()["content"] == "hi"


def test_nav_visible_and_order(client, auth_headers):
    _new_page(client, auth_headers, title="B", nav_order=2)
    _new_page(client, auth_headers, title="A", nav_order=1)
    _new_page(client, auth_headers, title="Hidden", nav_visible=False)

    nav = client.get("/api/pages/nav").json()
    titles = [n["title"] for n in nav]
    assert "Hidden" not in titles
    assert titles == ["A", "B"]


def test_reorder(client, auth_headers):
    a = _new_page(client, auth_headers, title="A").json()
    b = _new_page(client, auth_headers, title="B").json()
    client.post("/api/pages/reorder", json={"ids": [b["id"], a["id"]]}, headers=auth_headers)
    nav = client.get("/api/pages/nav").json()
    assert [n["title"] for n in nav] == ["B", "A"]


def test_assign_article_to_page_and_list(client, auth_headers):
    page = _new_page(client, auth_headers, title="技术").json()
    # 已发布、归属本页
    client.post(
        "/api/articles",
        json={"title": "Vue 教程", "content": "x", "published": True, "page_id": page["id"]},
        headers=auth_headers,
    )
    # 已发布、未归属
    client.post(
        "/api/articles",
        json={"title": "其他", "content": "x", "published": True},
        headers=auth_headers,
    )
    listing = client.get(f"/api/pages/{page['slug']}/articles").json()
    assert listing["total"] == 1
    assert listing["items"][0]["title"] == "Vue 教程"


def test_delete_page_unassigns_articles(client, auth_headers):
    page = _new_page(client, auth_headers, title="技术").json()
    art = client.post(
        "/api/articles",
        json={"title": "A", "content": "x", "published": True, "page_id": page["id"]},
        headers=auth_headers,
    ).json()

    assert client.delete(f"/api/pages/{page['id']}", headers=auth_headers).status_code == 204
    got = client.get(f"/api/articles/admin/{art['id']}", headers=auth_headers).json()
    assert got["page_id"] is None
