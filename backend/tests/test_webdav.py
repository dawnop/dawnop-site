"""只读 WebDAV 适配层测试。"""
import base64

import pytest

from app.core import qiniu_client
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME


def _basic(username=ADMIN_USERNAME, password=ADMIN_PASSWORD):
    raw = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


@pytest.fixture
def stub_qiniu(monkeypatch):
    store: dict[str, tuple[bytes, str]] = {}

    def fake_proxy_upload(key, data, mime=None):
        store[key] = (data, mime or "application/octet-stream")
        return {"key": key, "hash": "h"}

    def fake_private_url(key, expires=None, attname=None, fop=None):
        return f"https://cdn.example.com/{key}?e=1&token=sig"

    monkeypatch.setattr(qiniu_client, "proxy_upload", fake_proxy_upload)
    monkeypatch.setattr(qiniu_client, "private_url", fake_private_url)
    return store


def _upload(client, headers, path, name, data=b"x", mime="text/plain"):
    return client.post(
        "/api/fm/upload",
        data={"path": path},
        files={"file": (name, data, mime)},
        headers=headers,
    )


def test_options_advertises_dav_class1(client):
    r = client.request("OPTIONS", "/dav/")
    assert r.status_code == 200
    assert "1" in r.headers.get("DAV", "")
    allow = r.headers.get("Allow", "")
    assert "PROPFIND" in allow and "GET" in allow
    # 只读：不广播写方法
    assert "PUT" not in allow and "DELETE" not in allow


def test_requires_basic_auth(client):
    r = client.request("PROPFIND", "/dav/", headers={"Depth": "0"})
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate", "").startswith("Basic")


def test_bad_credentials_rejected(client):
    r = client.request("PROPFIND", "/dav/", headers={**_basic(password="wrong"), "Depth": "0"})
    assert r.status_code == 401


def test_propfind_lists_root_children(client, auth_headers, stub_qiniu):
    _upload(client, auth_headers, "qiniu://", "readme.txt", b"hello", "text/plain")
    _upload(client, auth_headers, "qiniu://docs", "a.md", b"# a", "text/markdown")

    r = client.request("PROPFIND", "/dav/", headers={**_basic(), "Depth": "1"})
    assert r.status_code == 207
    body = r.text
    # 根自身 + 文件 + 子目录
    assert "<D:href>/dav/</D:href>" in body
    assert "/dav/readme.txt" in body
    assert "/dav/docs/" in body  # 目录 href 带尾斜杠
    assert "<D:collection/>" in body
    assert "<D:getcontentlength>5</D:getcontentlength>" in body


def test_propfind_depth0_only_self(client, auth_headers, stub_qiniu):
    _upload(client, auth_headers, "qiniu://", "x.txt", b"xy", "text/plain")
    r = client.request("PROPFIND", "/dav/", headers={**_basic(), "Depth": "0"})
    assert r.status_code == 207
    assert r.text.count("<D:response>") == 1


def test_propfind_missing_is_404(client, stub_qiniu):
    r = client.request("PROPFIND", "/dav/nope/x.txt", headers={**_basic(), "Depth": "0"})
    assert r.status_code == 404


def test_get_default_client_proxies_bytes(client, auth_headers, stub_qiniu, monkeypatch):
    _upload(client, auth_headers, "qiniu://", "hi.txt", b"PROXY-BODY", "text/plain")

    class FakeResp:
        status_code = 200
        headers = {"Content-Length": "10"}

        def iter_content(self, n):
            yield b"PROXY-BODY"

    monkeypatch.setattr(
        "app.api.webdav._plain_http.get", lambda *a, **k: FakeResp()
    )
    # 默认 UA（非白名单）→ 后端代理，直接拿到字节
    r = client.request("GET", "/dav/hi.txt", headers=_basic())
    assert r.status_code == 200
    assert r.content == b"PROXY-BODY"


def test_get_redirect_client_gets_302(client, auth_headers, stub_qiniu):
    _upload(client, auth_headers, "qiniu://", "hi.txt", b"x", "text/plain")
    # rclone UA → 302 直连七牛
    r = client.request(
        "GET", "/dav/hi.txt",
        headers={**_basic(), "User-Agent": "rclone/v1.65"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "token=sig" in r.headers["location"]


def test_get_range_forwarded_when_proxying(client, auth_headers, stub_qiniu, monkeypatch):
    _upload(client, auth_headers, "qiniu://", "big.bin", b"0123456789", "application/octet-stream")
    seen = {}

    class FakeResp:
        status_code = 206
        headers = {"Content-Length": "3", "Content-Range": "bytes 2-4/10"}

        def iter_content(self, n):
            yield b"234"

    def fake_get(url, stream=None, timeout=None, headers=None):
        seen["range"] = (headers or {}).get("Range")
        return FakeResp()

    monkeypatch.setattr("app.api.webdav._plain_http.get", fake_get)
    r = client.request("GET", "/dav/big.bin", headers={**_basic(), "Range": "bytes=2-4"})
    assert r.status_code == 206
    assert seen["range"] == "bytes=2-4"
    assert r.headers.get("Content-Range") == "bytes 2-4/10"


def test_head_returns_metadata_no_body(client, auth_headers, stub_qiniu):
    _upload(client, auth_headers, "qiniu://", "hi.txt", b"hello!!", "text/plain")
    r = client.request("HEAD", "/dav/hi.txt", headers=_basic())
    assert r.status_code == 200
    assert r.headers.get("Content-Length") == "7"
    assert r.headers.get("Accept-Ranges") == "bytes"
    assert r.content == b""


def test_get_on_directory_405(client, auth_headers, stub_qiniu):
    _upload(client, auth_headers, "qiniu://docs", "a.md", b"# a", "text/markdown")
    r = client.request("GET", "/dav/docs", headers=_basic())
    assert r.status_code == 405


def test_write_methods_not_allowed(client, stub_qiniu):
    for method in ("PUT", "DELETE", "MKCOL", "MOVE", "COPY", "LOCK"):
        r = client.request(method, "/dav/x.txt", headers=_basic())
        assert r.status_code == 405, f"{method} should be 405"
