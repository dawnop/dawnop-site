def _post(client, headers, **over):
    payload = {"title": "T", "content": "x", "published": True}
    payload.update(over)
    return client.post("/api/articles", json=payload, headers=headers)


def test_create_with_tags(client, auth_headers):
    resp = _post(client, auth_headers, title="Post A", tags=["CUDA", "GPU"])
    assert resp.status_code == 201
    assert sorted(t["name"] for t in resp.json()["tags"]) == ["CUDA", "GPU"]


def test_tags_case_insensitive_reuse(client, auth_headers):
    a = _post(client, auth_headers, title="A", tags=["CUDA"]).json()
    b = _post(client, auth_headers, title="B", tags=["cuda"]).json()
    # 大小写不同视为同一标签（名以首次为准）
    assert a["tags"][0]["id"] == b["tags"][0]["id"]


def test_public_tags_list_counts_only_published(client, auth_headers):
    _post(client, auth_headers, title="P", tags=["x"], published=True)
    _post(client, auth_headers, title="D", tags=["x", "y"], published=False)
    tags = {t["slug"]: t["count"] for t in client.get("/api/tags").json()}
    assert tags.get("x") == 1     # x 有 1 篇已发布
    assert "y" not in tags        # y 仅被草稿使用 → 不出现在公开标签列表


def test_filter_articles_by_tag(client, auth_headers):
    _post(client, auth_headers, title="HasTag", tags=["CUDA"], published=True)
    _post(client, auth_headers, title="NoTag", published=True)
    body = client.get("/api/articles?tag=cuda").json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "HasTag"
    assert body["items"][0]["tags"][0]["slug"] == "cuda"


def test_update_replaces_tags(client, auth_headers):
    art = _post(client, auth_headers, title="U", tags=["a", "b"]).json()
    upd = client.put(
        f"/api/articles/{art['id']}", json={"tags": ["b", "c"]}, headers=auth_headers
    ).json()
    assert sorted(t["name"] for t in upd["tags"]) == ["b", "c"]


def test_tags_admin_requires_auth(client):
    assert client.get("/api/tags/admin").status_code == 401
