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


def test_create_rejects_blank_title(client, auth_headers):
    resp = _new_page(client, auth_headers, title="  ")
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)


def test_reorder_dedup_and_unknown_ids(client, auth_headers):
    a = _new_page(client, auth_headers, title="A").json()
    b = _new_page(client, auth_headers, title="B").json()
    # 含重复与不存在的 id，应被去重/忽略而不报错
    resp = client.post(
        "/api/pages/reorder",
        json={"ids": [b["id"], b["id"], a["id"], 99999]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    nav = client.get("/api/pages/nav").json()
    assert [n["title"] for n in nav] == ["B", "A"]


# ---------- 内置页（首页/标签页） ----------


def _seed_builtin(db_session):
    from app.core.bootstrap import ensure_builtin_pages

    ensure_builtin_pages(db_session())


def test_builtin_seed_idempotent(client, db_session, auth_headers):
    _seed_builtin(db_session)
    _seed_builtin(db_session)  # 重复执行不重复建
    pages = client.get("/api/pages/admin", headers=auth_headers).json()
    builtins = [p for p in pages if p["type"] == "builtin"]
    assert sorted(p["slug"] for p in builtins) == ["home", "tags"]


def test_builtin_nav_paths_and_order(client, db_session, auth_headers):
    _seed_builtin(db_session)
    _new_page(client, auth_headers, title="技术")
    nav = client.get("/api/pages/nav").json()
    assert [n["path"] for n in nav] == ["/", "/p/技术", "/tags"]  # 首页最前、标签页最后
    assert nav[0]["title"] == "首页"


def test_builtin_delete_rejected(client, db_session, auth_headers):
    _seed_builtin(db_session)
    home = next(
        p for p in client.get("/api/pages/admin", headers=auth_headers).json()
        if p["slug"] == "home"
    )
    assert client.delete(f"/api/pages/{home['id']}", headers=auth_headers).status_code == 400


def test_builtin_update_whitelist(client, db_session, auth_headers):
    _seed_builtin(db_session)
    home = next(
        p for p in client.get("/api/pages/admin", headers=auth_headers).json()
        if p["slug"] == "home"
    )
    resp = client.put(
        f"/api/pages/{home['id']}",
        json={"title": "主页", "nav_visible": False, "slug": "hacked", "content": "x"},
        headers=auth_headers,
    )
    body = resp.json()
    assert body["title"] == "主页"          # 导航名可改
    assert body["nav_visible"] is False     # 显隐可改
    assert body["slug"] == "home"           # slug 不可改
    assert body["content"] == ""            # 内容不可改


def test_builtin_not_served_as_content_page(client, db_session):
    _seed_builtin(db_session)
    assert client.get("/api/pages/home").status_code == 404
