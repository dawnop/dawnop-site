def _new_viz(client, headers, **over):
    payload = {
        "slug": "demo-viz",
        "name": "示例",
        "source": "<template><div>hi</div></template>",
        "compiled": "return function(Vue){return {}}",
        "style": "",
    }
    payload.update(over)
    return client.post("/api/viz", json=payload, headers=headers)


def test_create_requires_auth(client):
    assert _new_viz(client, {}).status_code == 401


def test_create_and_public_get(client, auth_headers):
    resp = _new_viz(client, auth_headers)
    assert resp.status_code == 201
    viz = resp.json()
    assert viz["slug"] == "demo-viz"

    # 公开读取：只含挂载所需字段，不含 source
    got = client.get("/api/viz/demo-viz")
    assert got.status_code == 200
    body = got.json()
    assert body["compiled"].startswith("return")
    assert "source" not in body


def test_list_requires_auth(client, auth_headers):
    _new_viz(client, auth_headers)
    assert client.get("/api/viz").status_code == 401
    listing = client.get("/api/viz", headers=auth_headers).json()
    assert any(v["slug"] == "demo-viz" for v in listing)


def test_invalid_slug_rejected(client, auth_headers):
    resp = _new_viz(client, auth_headers, slug="Bad Slug!")
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)


def test_blank_source_rejected(client, auth_headers):
    resp = _new_viz(client, auth_headers, source="   ")
    assert resp.status_code == 422


def test_duplicate_slug_conflict(client, auth_headers):
    assert _new_viz(client, auth_headers).status_code == 201
    dup = _new_viz(client, auth_headers, name="另一个")
    assert dup.status_code == 409


def test_update_and_delete(client, auth_headers):
    viz = _new_viz(client, auth_headers).json()
    upd = client.put(
        f"/api/viz/{viz['id']}",
        json={"name": "改名", "source": "<template><b>x</b></template>"},
        headers=auth_headers,
    )
    assert upd.status_code == 200
    assert upd.json()["name"] == "改名"

    assert client.delete(f"/api/viz/{viz['id']}", headers=auth_headers).status_code == 204
    assert client.get("/api/viz/demo-viz").status_code == 404


def test_public_get_missing_404(client):
    assert client.get("/api/viz/nope").status_code == 404
