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


def test_admin_list_counts_include_drafts(client, auth_headers):
    _post(client, auth_headers, title="P", tags=["x"], published=True)
    _post(client, auth_headers, title="D", tags=["x"], published=False)
    tags = {t["slug"]: t["count"] for t in client.get("/api/tags/admin", headers=auth_headers).json()}
    assert tags["x"] == 2  # 草稿也计入后台文章数


def test_get_tag_by_slug_public(client, auth_headers):
    _post(client, auth_headers, title="P", tags=["CUDA"], published=True)
    _post(client, auth_headers, title="D", tags=["CUDA"], published=False)
    body = client.get("/api/tags/cuda").json()
    assert body["name"] == "CUDA"
    assert body["count"] == 1  # 公开 count 只算已发布
    assert client.get("/api/tags/nope").status_code == 404


def test_rename_tag(client, auth_headers):
    art = _post(client, auth_headers, title="A", tags=["oldname"]).json()
    tag_id = art["tags"][0]["id"]
    resp = client.put(f"/api/tags/{tag_id}", json={"name": "NewName"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "NewName"
    assert resp.json()["slug"] == "newname"  # slug 联动重生成
    # 文章侧同步可见新名
    assert client.get(f"/api/articles/{art['slug']}").json()["tags"][0]["name"] == "NewName"


def test_rename_tag_duplicate_rejected(client, auth_headers):
    art = _post(client, auth_headers, title="A", tags=["a", "b"]).json()
    ids = {t["name"]: t["id"] for t in art["tags"]}
    resp = client.put(f"/api/tags/{ids['a']}", json={"name": "B"}, headers=auth_headers)
    assert resp.status_code == 400  # 与既有标签重名（忽略大小写）


def test_delete_tag_detaches_articles(client, auth_headers):
    art = _post(client, auth_headers, title="A", tags=["gone", "keep"]).json()
    tag_id = next(t["id"] for t in art["tags"] if t["name"] == "gone")
    assert client.delete(f"/api/tags/{tag_id}", headers=auth_headers).status_code == 204
    names = [t["name"] for t in client.get(f"/api/articles/{art['slug']}").json()["tags"]]
    assert names == ["keep"]


def test_merge_tags(client, auth_headers):
    a = _post(client, auth_headers, title="A", tags=["src"]).json()
    b = _post(client, auth_headers, title="B", tags=["src", "dst"]).json()
    src_id = a["tags"][0]["id"]
    dst_id = next(t["id"] for t in b["tags"] if t["name"] == "dst")
    resp = client.post(
        "/api/tags/merge", json={"source_id": src_id, "target_id": dst_id}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 2  # A、B 都归到 dst（B 原本已有，不重复）
    # src 已删除
    slugs = [t["slug"] for t in client.get("/api/tags/admin", headers=auth_headers).json()]
    assert "src" not in slugs
    assert [t["name"] for t in client.get(f"/api/articles/{a['slug']}").json()["tags"]] == ["dst"]


def test_merge_with_self_rejected(client, auth_headers):
    art = _post(client, auth_headers, title="A", tags=["x"]).json()
    tag_id = art["tags"][0]["id"]
    resp = client.post(
        "/api/tags/merge", json={"source_id": tag_id, "target_id": tag_id}, headers=auth_headers
    )
    assert resp.status_code == 400


def test_cleanup_removes_unused_tags(client, auth_headers):
    art = _post(client, auth_headers, title="A", tags=["used", "orphan"]).json()
    # 把文章标签改成只剩 used，orphan 成为零文章标签
    client.put(f"/api/articles/{art['id']}", json={"tags": ["used"]}, headers=auth_headers)
    resp = client.post("/api/tags/cleanup", headers=auth_headers)
    assert resp.json()["deleted"] == 1
    slugs = [t["slug"] for t in client.get("/api/tags/admin", headers=auth_headers).json()]
    assert slugs == ["used"]


def test_export_frontmatter_includes_tags(client, auth_headers):
    art = _post(client, auth_headers, title="E", tags=["CUDA", "GPU"]).json()
    text = client.get(f"/api/articles/{art['id']}/export", headers=auth_headers).text
    assert "tags: [CUDA, GPU]" in text
