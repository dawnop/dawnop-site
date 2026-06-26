def _create(client, headers, **over):
    payload = {
        "title": "Hello World",
        "summary": "first post",
        "content": "# Hi\n\n$E=mc^2$",
        "published": True,
    }
    payload.update(over)
    return client.post("/api/articles", json=payload, headers=headers)


def test_create_requires_auth(client):
    assert _create(client, {}).status_code == 401


def test_create_and_get_by_slug(client, auth_headers):
    resp = _create(client, auth_headers)
    assert resp.status_code == 201
    art = resp.json()
    assert art["slug"] == "hello-world"

    pub = client.get(f"/api/articles/{art['slug']}")
    assert pub.status_code == 200
    assert pub.json()["content"].startswith("# Hi")


def test_public_list_hides_drafts(client, auth_headers):
    _create(client, auth_headers, title="Published One", published=True)
    _create(client, auth_headers, title="Draft One", published=False)

    resp = client.get("/api/articles")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Published One"
    # 列表项不含正文
    assert "content" not in body["items"][0]


def test_draft_not_public_but_admin_sees_it(client, auth_headers):
    resp = _create(client, auth_headers, title="Secret", published=False)
    slug = resp.json()["slug"]

    # 匿名按 slug 取草稿 → 404；登录后凭直链可预览 → 200
    assert client.get(f"/api/articles/{slug}").status_code == 404
    preview = client.get(f"/api/articles/{slug}", headers=auth_headers)
    assert preview.status_code == 200
    assert preview.json()["title"] == "Secret"

    admin = client.get("/api/articles/admin", headers=auth_headers)
    assert admin.json()["total"] == 1


def test_slug_uniqueness(client, auth_headers):
    a = _create(client, auth_headers, title="Same Title").json()
    b = _create(client, auth_headers, title="Same Title").json()
    assert a["slug"] == "same-title"
    assert b["slug"] == "same-title-2"


def test_update_article(client, auth_headers):
    art = _create(client, auth_headers, published=False).json()
    resp = client.put(
        f"/api/articles/{art['id']}",
        json={"title": "Updated", "published": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Updated"
    # slug 未显式修改则保持不变
    assert resp.json()["slug"] == "hello-world"
    assert client.get("/api/articles/hello-world").status_code == 200


def test_delete_article(client, auth_headers):
    art = _create(client, auth_headers).json()
    assert (
        client.delete(f"/api/articles/{art['id']}", headers=auth_headers).status_code
        == 204
    )
    assert (
        client.get(f"/api/articles/admin/{art['id']}", headers=auth_headers).status_code
        == 404
    )


def test_export_markdown(client, auth_headers):
    md = "# Imported Title\n\nbody with $\\alpha$"
    art = _create(client, auth_headers, title="Imported Title", content=md).json()

    exported = client.get(
        f"/api/articles/{art['id']}/export", headers=auth_headers
    )
    assert exported.status_code == 200
    assert exported.text == md
    assert "attachment" in exported.headers["content-disposition"]


def test_import_endpoint_removed(client, auth_headers):
    # 导入已下线（改前端复用新建流程）
    resp = client.post(
        "/api/articles/import",
        files={"file": ("x.md", b"# X", "text/markdown")},
        headers=auth_headers,
    )
    assert resp.status_code in (404, 405)


def test_auto_title_flag_roundtrip(client, auth_headers):
    # auto_title 仅作为标志存取；正文完整保留（前端负责派生标题与渲染去重）
    md = "# 标题来自正文\n\n正文段落"
    resp = _create(
        client, auth_headers, title="标题来自正文", content=md, auto_title=True
    )
    assert resp.status_code == 201
    art = resp.json()
    assert art["auto_title"] is True
    assert art["content"] == md  # 后端不剥离正文

    got = client.get(f"/api/articles/{art['slug']}", headers=auth_headers).json()
    assert got["auto_title"] is True

    # 关闭开关
    upd = client.put(
        f"/api/articles/{art['id']}", json={"auto_title": False}, headers=auth_headers
    )
    assert upd.json()["auto_title"] is False


def test_create_rejects_blank_title(client, auth_headers):
    resp = _create(client, auth_headers, title="   ")
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)


def test_create_rejects_unknown_page_id(client, auth_headers):
    resp = _create(client, auth_headers, page_id=9999)
    assert resp.status_code == 400


def test_create_rejects_content_page_id(client, auth_headers):
    # 内容页不能作为文章所属列表页
    page = client.post(
        "/api/pages", json={"title": "关于", "type": "content"}, headers=auth_headers
    ).json()
    resp = _create(client, auth_headers, page_id=page["id"])
    assert resp.status_code == 400


def test_update_rejects_content_page_id(client, auth_headers):
    art = _create(client, auth_headers).json()
    page = client.post(
        "/api/pages", json={"title": "随笔", "type": "content"}, headers=auth_headers
    ).json()
    resp = client.put(
        f"/api/articles/{art['id']}",
        json={"page_id": page["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 400
