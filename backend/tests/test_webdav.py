"""可读写 WebDAV 适配层测试。"""
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

    def fake_proxy_upload_file(key, filepath, mime=None):
        with open(filepath, "rb") as f:
            store[key] = (f.read(), mime or "application/octet-stream")
        return {"key": key, "hash": "h"}

    def fake_private_url(key, expires=None, attname=None, fop=None):
        return f"https://cdn.example.com/{key}?e=1&token=sig"

    def fake_delete(key):
        store.pop(key, None)

    def fake_copy(src, dst):
        store[dst] = store.get(src, (b"", "application/octet-stream"))

    monkeypatch.setattr(qiniu_client, "proxy_upload", fake_proxy_upload)
    monkeypatch.setattr(qiniu_client, "proxy_upload_file", fake_proxy_upload_file)
    monkeypatch.setattr(qiniu_client, "private_url", fake_private_url)
    monkeypatch.setattr(qiniu_client, "delete", fake_delete)
    monkeypatch.setattr(qiniu_client, "copy", fake_copy)
    return store


def _upload(client, headers, path, name, data=b"x", mime="text/plain"):
    return client.post(
        "/api/fm/upload",
        data={"path": path},
        files={"file": (name, data, mime)},
        headers=headers,
    )


def test_options_advertises_dav_class2_and_write(client):
    r = client.request("OPTIONS", "/dav/")
    assert r.status_code == 200
    dav = r.headers.get("DAV", "")
    assert "1" in dav and "2" in dav  # class 2 = LOCK，Finder 据此可写挂载
    allow = r.headers.get("Allow", "")
    for m in ("PROPFIND", "GET", "PUT", "DELETE", "MKCOL", "MOVE", "COPY", "LOCK"):
        assert m in allow


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


def test_propfind_root_prefix_via_header(client, auth_headers, stub_qiniu):
    """子域名 vhost（nginx 传 X-Dav-Prefix: /）→ href 无 /dav 前缀，走域名根。"""
    _upload(client, auth_headers, "qiniu://", "readme.txt", b"hello", "text/plain")
    _upload(client, auth_headers, "qiniu://docs", "a.md", b"# a", "text/markdown")

    r = client.request(
        "PROPFIND", "/dav/", headers={**_basic(), "Depth": "1", "X-Dav-Prefix": "/"}
    )
    assert r.status_code == 207
    body = r.text
    assert "<D:href>/</D:href>" in body          # 根
    assert "<D:href>/readme.txt</D:href>" in body
    assert "<D:href>/docs/</D:href>" in body
    assert "/dav/" not in body                    # 绝不带 /dav 前缀


def test_move_root_prefix_destination(client, stub_qiniu):
    """子域名下 Destination 形如 https://dav.dawnop.com/new.txt（无 /dav）。"""
    client.request("PUT", "/dav/old.txt", headers=_basic(), content=b"data")
    r = client.request(
        "MOVE", "/dav/old.txt",
        headers={
            **_basic(),
            "X-Dav-Prefix": "/",
            "Destination": "https://dav.dawnop.com/new.txt",
        },
    )
    assert r.status_code == 201
    assert client.request(
        "PROPFIND", "/dav/new.txt", headers={**_basic(), "Depth": "0"}
    ).status_code == 207


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
    assert r.headers.get("ETag", "").startswith('"')
    assert r.content == b""


def test_propfind_includes_etag(client, auth_headers, stub_qiniu):
    _upload(client, auth_headers, "qiniu://", "e.txt", b"x", "text/plain")
    r = client.request("PROPFIND", "/dav/e.txt", headers={**_basic(), "Depth": "0"})
    assert r.status_code == 207
    assert "<D:getetag>" in r.text


def test_if_none_match_returns_304(client, auth_headers, stub_qiniu):
    _upload(client, auth_headers, "qiniu://", "c.txt", b"cache me", "text/plain")
    etag = client.request(
        "HEAD", "/dav/c.txt", headers=_basic()
    ).headers["ETag"]
    # 拿着相同 ETag 复验 → 304，无 body
    r = client.request(
        "GET", "/dav/c.txt", headers={**_basic(), "If-None-Match": etag}
    )
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers.get("ETag") == etag


def test_if_none_match_mismatch_serves_full(client, auth_headers, stub_qiniu, monkeypatch):
    _upload(client, auth_headers, "qiniu://", "c.txt", b"FULLBODY", "text/plain")

    class FakeResp:
        status_code = 200
        headers = {"Content-Length": "8"}

        def iter_content(self, n):
            yield b"FULLBODY"

    monkeypatch.setattr("app.api.webdav._plain_http.get", lambda *a, **k: FakeResp())
    # ETag 不匹配 → 回全量
    r = client.request(
        "GET", "/dav/c.txt", headers={**_basic(), "If-None-Match": '"stale-key"'}
    )
    assert r.status_code == 200
    assert r.content == b"FULLBODY"


def test_get_on_directory_405(client, auth_headers, stub_qiniu):
    _upload(client, auth_headers, "qiniu://docs", "a.md", b"# a", "text/markdown")
    r = client.request("GET", "/dav/docs", headers=_basic())
    assert r.status_code == 405


def test_write_requires_auth(client, stub_qiniu):
    r = client.request("PUT", "/dav/x.txt", content=b"hi")
    assert r.status_code == 401


def test_put_creates_file(client, stub_qiniu):
    r = client.request("PUT", "/dav/note.txt", headers=_basic(), content=b"hello dav")
    assert r.status_code == 201
    # 落地：能 PROPFIND 看到、能 GET 回读（默认 UA 走后端代理）
    r = client.request("PROPFIND", "/dav/", headers={**_basic(), "Depth": "1"})
    assert "/dav/note.txt" in r.text
    assert "<D:getcontentlength>9</D:getcontentlength>" in r.text


def test_put_streams_body_intact(client, stub_qiniu):
    # 多块 body 走临时文件流式上传，逐字节应与上传内容一致
    payload = b"streamed-body-" * 5000
    r = client.request("PUT", "/dav/big.txt", headers=_basic(), content=payload)
    assert r.status_code == 201
    assert any(data == payload for data, _ in stub_qiniu.values())
    # PROPFIND 的 size 也应等于实际字节数
    r = client.request("PROPFIND", "/dav/big.txt", headers={**_basic(), "Depth": "0"})
    assert f"<D:getcontentlength>{len(payload)}</D:getcontentlength>" in r.text


def test_put_overwrite_returns_204(client, stub_qiniu):
    client.request("PUT", "/dav/a.txt", headers=_basic(), content=b"v1")
    r = client.request("PUT", "/dav/a.txt", headers=_basic(), content=b"v2-longer")
    assert r.status_code == 204
    r = client.request("PROPFIND", "/dav/a.txt", headers={**_basic(), "Depth": "0"})
    assert "<D:getcontentlength>9</D:getcontentlength>" in r.text


def test_put_missing_parent_409(client, stub_qiniu):
    r = client.request("PUT", "/dav/nope/x.txt", headers=_basic(), content=b"x")
    assert r.status_code == 409


def test_mkcol_creates_dir(client, stub_qiniu):
    r = client.request("MKCOL", "/dav/docs", headers=_basic())
    assert r.status_code == 201
    r = client.request("PROPFIND", "/dav/", headers={**_basic(), "Depth": "1"})
    assert "/dav/docs/" in r.text and "<D:collection/>" in r.text


def test_mkcol_existing_405(client, stub_qiniu):
    client.request("MKCOL", "/dav/docs", headers=_basic())
    r = client.request("MKCOL", "/dav/docs", headers=_basic())
    assert r.status_code == 405


def test_delete_removes_subtree(client, stub_qiniu):
    client.request("MKCOL", "/dav/docs", headers=_basic())
    client.request("PUT", "/dav/docs/a.txt", headers=_basic(), content=b"a")
    r = client.request("DELETE", "/dav/docs", headers=_basic())
    assert r.status_code == 204
    r = client.request("PROPFIND", "/dav/docs", headers={**_basic(), "Depth": "0"})
    assert r.status_code == 404


def test_delete_missing_404(client, stub_qiniu):
    r = client.request("DELETE", "/dav/ghost.txt", headers=_basic())
    assert r.status_code == 404


def test_move_renames(client, stub_qiniu):
    client.request("PUT", "/dav/old.txt", headers=_basic(), content=b"data")
    r = client.request(
        "MOVE", "/dav/old.txt",
        headers={**_basic(), "Destination": "http://testserver/dav/new.txt"},
    )
    assert r.status_code == 201
    assert client.request("PROPFIND", "/dav/old.txt", headers={**_basic(), "Depth": "0"}).status_code == 404
    assert client.request("PROPFIND", "/dav/new.txt", headers={**_basic(), "Depth": "0"}).status_code == 207


def test_move_overwrite_flag(client, stub_qiniu):
    client.request("PUT", "/dav/a.txt", headers=_basic(), content=b"a")
    client.request("PUT", "/dav/b.txt", headers=_basic(), content=b"b")
    # Overwrite: F → 已存在则 412
    r = client.request(
        "MOVE", "/dav/a.txt",
        headers={**_basic(), "Destination": "http://testserver/dav/b.txt", "Overwrite": "F"},
    )
    assert r.status_code == 412
    # 默认覆盖 → 204
    r = client.request(
        "MOVE", "/dav/a.txt",
        headers={**_basic(), "Destination": "http://testserver/dav/b.txt"},
    )
    assert r.status_code == 204


def test_copy_duplicates(client, stub_qiniu):
    client.request("PUT", "/dav/src.txt", headers=_basic(), content=b"payload")
    r = client.request(
        "COPY", "/dav/src.txt",
        headers={**_basic(), "Destination": "http://testserver/dav/dst.txt"},
    )
    assert r.status_code == 201
    # 源仍在、副本也在
    assert client.request("PROPFIND", "/dav/src.txt", headers={**_basic(), "Depth": "0"}).status_code == 207
    assert client.request("PROPFIND", "/dav/dst.txt", headers={**_basic(), "Depth": "0"}).status_code == 207


def test_lock_grants_token(client, stub_qiniu):
    r = client.request("LOCK", "/dav/whatever.txt", headers=_basic())
    assert r.status_code == 200
    assert r.headers.get("Lock-Token", "").startswith("<opaquelocktoken:")
    assert "<D:write/>" in r.text


def test_unlock_204(client, stub_qiniu):
    r = client.request("UNLOCK", "/dav/whatever.txt", headers=_basic())
    assert r.status_code == 204
