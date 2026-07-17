"""全局配置端点单测。"""


def test_settings_require_auth(client):
    assert client.get("/api/settings").status_code == 401
    assert client.put("/api/settings", json={}).status_code == 401


def test_settings_defaults_and_update(client, auth_headers):
    r = client.get("/api/settings", headers=auth_headers).json()
    assert r["upload_concurrency"] == 3
    assert r["download_concurrency"] == 3
    assert r["storage_quota_gb"] >= 1
    assert r["text_preview_max_kb"] == 512

    r = client.put(
        "/api/settings",
        json={"upload_concurrency": 5, "storage_quota_gb": 20},
        headers=auth_headers,
    ).json()
    assert r["upload_concurrency"] == 5
    assert r["storage_quota_gb"] == 20
    # 再读一次确认持久化
    r = client.get("/api/settings", headers=auth_headers).json()
    assert r["upload_concurrency"] == 5


def test_settings_validation(client, auth_headers):
    assert (
        client.put("/api/settings", json={"nope": 1}, headers=auth_headers).status_code
        == 400
    )
    assert (
        client.put(
            "/api/settings", json={"upload_concurrency": 0}, headers=auth_headers
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/settings", json={"upload_concurrency": "3"}, headers=auth_headers
        ).status_code
        == 400
    )
