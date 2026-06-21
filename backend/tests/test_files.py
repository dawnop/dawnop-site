"""文件模块单测：用 monkeypatch 打桩七牛调用，不触网。"""
import pytest

from app.core import qiniu_client


@pytest.fixture
def stub_qiniu(monkeypatch):
    """把 qiniu_client 的网络调用替换为内存桩。"""
    store: dict[str, bytes] = {}

    def fake_upload_token(key, expires=None):
        return f"token-for-{key}"

    def fake_proxy_upload(key, data, mime=None):
        store[key] = data
        return {"key": key, "hash": "fakehash"}

    def fake_private_url(key, expires=None):
        return f"https://cdn.example.com/{key}?e=123&token=sig"

    def fake_stat(key):
        return {"fsize": len(store.get(key, b"")), "mimeType": "text/plain"}

    def fake_delete(key):
        store.pop(key, None)

    monkeypatch.setattr(qiniu_client, "upload_token", fake_upload_token)
    monkeypatch.setattr(qiniu_client, "proxy_upload", fake_proxy_upload)
    monkeypatch.setattr(qiniu_client, "private_url", fake_private_url)
    monkeypatch.setattr(qiniu_client, "stat", fake_stat)
    monkeypatch.setattr(qiniu_client, "delete", fake_delete)
    return store


def test_files_require_auth(client):
    assert client.get("/api/files").status_code == 401


def test_proxy_upload_then_list_url_delete(client, auth_headers, stub_qiniu):
    # 上传
    resp = client.post(
        "/api/files/upload",
        files={"file": ("pic.png", b"\x89PNG\r\n\x1a\n binarydata", "image/png")},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    obj = resp.json()
    assert obj["filename"] == "pic.png"
    assert obj["content_type"] == "image/png"
    assert obj["size"] == len(b"\x89PNG\r\n\x1a\n binarydata")
    assert obj["key"].endswith(".png")
    file_id = obj["id"]

    # 列表
    listing = client.get("/api/files", headers=auth_headers).json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == file_id

    # 预览/下载 URL（私有签名）
    url_resp = client.get(f"/api/files/{file_id}/url", headers=auth_headers)
    assert url_resp.status_code == 200
    assert "token=sig" in url_resp.json()["url"]

    # 删除
    assert (
        client.delete(f"/api/files/{file_id}", headers=auth_headers).status_code == 204
    )
    assert client.get("/api/files", headers=auth_headers).json()["total"] == 0


def test_upload_token_and_register(client, auth_headers, stub_qiniu):
    tok = client.post(
        "/api/files/upload-token",
        json={"filename": "notes.txt"},
        headers=auth_headers,
    )
    assert tok.status_code == 200
    body = tok.json()
    assert body["key"].endswith(".txt")
    assert body["token"].startswith("token-for-")

    # 模拟前端直传成功后登记（size/mime 缺省，由 stat 补全）
    reg = client.post(
        "/api/files/register",
        json={"key": body["key"], "filename": "notes.txt"},
        headers=auth_headers,
    )
    assert reg.status_code == 201
    assert reg.json()["content_type"] == "text/plain"


def test_url_404_for_missing(client, auth_headers, stub_qiniu):
    assert client.get("/api/files/999/url", headers=auth_headers).status_code == 404
