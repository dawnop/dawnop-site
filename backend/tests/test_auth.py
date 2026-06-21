def test_login_success(client, admin_creds):
    resp = client.post("/api/auth/login", data=admin_creds)
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_wrong_password(client, admin_creds):
    resp = client.post(
        "/api/auth/login",
        data={"username": admin_creds["username"], "password": "wrong"},
    )
    assert resp.status_code == 401


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_with_token(client, auth_headers, admin_creds):
    resp = client.get("/api/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["username"] == admin_creds["username"]
