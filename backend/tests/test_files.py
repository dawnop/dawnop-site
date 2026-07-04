"""VueFinder 文件管理端点单测：打桩七牛，不触网。"""
import pytest

from app.core import qiniu_client


@pytest.fixture
def stub_qiniu(monkeypatch):
    """内存桩：key -> (bytes, mime)。"""
    store: dict[str, tuple[bytes, str]] = {}

    def fake_proxy_upload(key, data, mime=None):
        store[key] = (data, mime or "application/octet-stream")
        return {"key": key, "hash": "fakehash"}

    def fake_private_url(key, expires=None, attname=None):
        suffix = f"&attname={attname}" if attname else ""
        return f"https://cdn.example.com/{key}?e=123&token=sig{suffix}"

    def fake_delete(key):
        store.pop(key, None)

    def fake_copy(src, dst):
        store[dst] = store[src]

    def fake_upload_token(key, expires=None):
        return f"uptoken-for-{key}"

    def fake_upload_host():
        return "https://up.example.com"

    def fake_stat(key):
        # 模拟 /register 的存在性校验：对象不在（未真正直传）则报错
        if key not in store:
            raise RuntimeError("no such object")
        data, mime = store[key]
        return {"fsize": len(data), "mimeType": mime, "hash": "fakehash"}

    monkeypatch.setattr(qiniu_client, "proxy_upload", fake_proxy_upload)
    monkeypatch.setattr(qiniu_client, "private_url", fake_private_url)
    monkeypatch.setattr(qiniu_client, "delete", fake_delete)
    monkeypatch.setattr(qiniu_client, "copy", fake_copy)
    monkeypatch.setattr(qiniu_client, "upload_token", fake_upload_token)
    monkeypatch.setattr(qiniu_client, "upload_host", fake_upload_host)
    monkeypatch.setattr(qiniu_client, "stat", fake_stat)
    return store


def _upload(client, headers, path, name, data=b"x", mime="text/plain"):
    return client.post(
        "/api/fm/upload",
        data={"path": path},
        files={"file": (name, data, mime)},
        headers=headers,
    )


def test_fm_requires_auth(client):
    assert client.get("/api/fm").status_code == 401
    assert client.get("/api/fm/preview?path=qiniu://a.txt").status_code == 401


def test_list_empty_root(client, auth_headers, stub_qiniu):
    r = client.get("/api/fm", headers=auth_headers).json()
    assert r["storages"] == ["qiniu"]
    assert r["dirname"] == "qiniu://"
    assert r["files"] == []


def test_upload_into_subfolder_creates_parents(client, auth_headers, stub_qiniu):
    r = _upload(client, auth_headers, "qiniu://docs/img", "cover.png",
                b"\x89PNG", "image/png")
    assert r.status_code == 200
    entry = r.json()
    assert entry["path"] == "qiniu://docs/img/cover.png"
    assert entry["type"] == "file"
    assert entry["mime_type"] == "image/png"

    # 根目录应出现自动创建的 docs 文件夹
    root = client.get("/api/fm", headers=auth_headers).json()
    assert [(f["basename"], f["type"]) for f in root["files"]] == [("docs", "dir")]

    # docs 下应有 img 文件夹
    docs = client.get("/api/fm?path=qiniu://docs", headers=auth_headers).json()
    assert docs["dirname"] == "qiniu://docs"
    assert [(f["basename"], f["type"]) for f in docs["files"]] == [("img", "dir")]

    # img 下应有文件，dir 字段指向父目录
    img = client.get("/api/fm?path=qiniu://docs/img", headers=auth_headers).json()
    assert img["files"][0]["dir"] == "qiniu://docs/img"


def test_create_folder_rename_move(client, auth_headers, stub_qiniu):
    assert client.post("/api/fm/create-folder",
                       json={"path": "qiniu://", "name": "a"},
                       headers=auth_headers).status_code == 200
    # 重命名 a -> b
    client.post("/api/fm/rename",
                json={"path": "qiniu://", "item": "qiniu://a", "name": "b"},
                headers=auth_headers)
    root = client.get("/api/fm", headers=auth_headers).json()
    assert [f["basename"] for f in root["files"]] == ["b"]

    # 上传文件到根，移动进 b
    _upload(client, auth_headers, "qiniu://", "note.txt")
    client.post("/api/fm/move",
                json={"path": "qiniu://", "sources": ["qiniu://note.txt"],
                      "destination": "qiniu://b"},
                headers=auth_headers)
    binner = client.get("/api/fm?path=qiniu://b", headers=auth_headers).json()
    assert [f["basename"] for f in binner["files"]] == ["note.txt"]


def test_copy_then_delete_subtree(client, auth_headers, stub_qiniu):
    _upload(client, auth_headers, "qiniu://src", "a.txt", b"hello")
    # 复制 src -> dst
    client.post("/api/fm/copy",
                json={"path": "qiniu://", "sources": ["qiniu://src"],
                      "destination": "qiniu://dst"},
                headers=auth_headers)
    # 复制是「放入」目标文件夹：dst/src/a.txt
    dst = client.get("/api/fm?path=qiniu://dst/src", headers=auth_headers).json()
    assert [f["basename"] for f in dst["files"]] == ["a.txt"]
    # 两份内容独立存在（不同 key）
    assert len(stub_qiniu) == 2

    # 删除整个 src 子树
    r = client.post("/api/fm/delete",
                    json={"path": "qiniu://",
                          "items": [{"path": "qiniu://src", "type": "dir"}]},
                    headers=auth_headers).json()
    assert any(d["path"] == "qiniu://src/a.txt" for d in r["deleted"])
    root = client.get("/api/fm", headers=auth_headers).json()
    assert [f["basename"] for f in root["files"]] == ["dst"]


def test_preview_via_header_and_token_query(client, auth_headers, stub_qiniu):
    _upload(client, auth_headers, "qiniu://", "hi.txt", b"PREVIEW-BODY", "text/plain")
    # 预览 = 302 跳到签名 URL（浏览器随后直连七牛，不经后端）
    r = client.get("/api/fm/preview?path=qiniu://hi.txt", headers=auth_headers,
                   follow_redirects=False)
    assert r.status_code == 302 and "token=sig" in r.headers["location"]
    # ?token= 方式（浏览器 <img>/下载场景，带不上 header）
    token = auth_headers["Authorization"].split()[1]
    r2 = client.get(f"/api/fm/preview?path=qiniu://hi.txt&token={token}",
                    follow_redirects=False)
    assert r2.status_code == 302 and "token=sig" in r2.headers["location"]
    # 下载：签名 URL 带 attname（强制附件下载）
    d = client.get(f"/api/fm/download?path=qiniu://hi.txt&token={token}",
                   follow_redirects=False)
    assert d.status_code == 302 and "attname=hi.txt" in d.headers["location"]
    # 未认证仍拒绝
    assert client.get("/api/fm/preview?path=qiniu://hi.txt",
                      follow_redirects=False).status_code == 401
    # /sign 返回签名 URL（供文本预览直连七牛）
    s = client.get("/api/fm/sign?path=qiniu://hi.txt", headers=auth_headers)
    assert s.status_code == 200 and "token=sig" in s.json()["url"]


def test_search_and_save(client, auth_headers, stub_qiniu):
    _upload(client, auth_headers, "qiniu://", "alpha.txt")
    _upload(client, auth_headers, "qiniu://sub", "beta.txt")
    deep = client.get("/api/fm/search?path=qiniu://&filter=beta&deep=1",
                      headers=auth_headers).json()
    assert [f["basename"] for f in deep["files"]] == ["beta.txt"]

    # save 覆盖文本内容（写回七牛，校验桩内容）
    client.post("/api/fm/save",
                json={"path": "qiniu://alpha.txt", "content": "NEW"},
                headers=auth_headers)
    assert any(v[0] == b"NEW" for v in stub_qiniu.values())


def test_direct_upload_token_then_register(client, auth_headers, stub_qiniu):
    # 1. 取直传凭证（限定 key、附上传域名）
    tok = client.post("/api/fm/upload-token",
                      json={"path": "qiniu://pics", "name": "a.png"},
                      headers=auth_headers).json()
    assert tok["key"].endswith(".png")
    assert tok["token"] == f"uptoken-for-{tok['key']}"
    assert tok["up_host"] == "https://up.example.com"
    assert tok["path"] == "qiniu://pics/a.png"

    # 2. 前端直传七牛（此处以入桩模拟），成功后登记 path↔key
    stub_qiniu[tok["key"]] = (b"x" * 1234, "image/png")
    reg = client.post("/api/fm/register",
                      json={"path": tok["path"], "key": tok["key"],
                            "size": 1234, "content_type": "image/png"},
                      headers=auth_headers)
    assert reg.status_code == 200
    entry = reg.json()
    assert entry["path"] == "qiniu://pics/a.png"
    # 大小以七牛 stat 的 fsize 为准
    assert entry["file_size"] == 1234

    # 自动建出的 pics 文件夹应出现在根
    root = client.get("/api/fm", headers=auth_headers).json()
    assert [(f["basename"], f["type"]) for f in root["files"]] == [("pics", "dir")]


def test_register_rejects_when_object_missing(client, auth_headers, stub_qiniu):
    """直传未真正落到七牛时，/register 应拒绝（不建悬空引用）。"""
    tok = client.post("/api/fm/upload-token",
                      json={"path": "qiniu://", "name": "ghost.txt"},
                      headers=auth_headers).json()
    # 不入桩：模拟对象并不存在
    reg = client.post("/api/fm/register",
                      json={"path": tok["path"], "key": tok["key"], "size": 9},
                      headers=auth_headers)
    assert reg.status_code == 400
    # 未建行；且待登记账本仍保留该 key（供后续孤儿清理识别）
    root = client.get("/api/fm", headers=auth_headers).json()
    assert root["files"] == []


def test_upload_token_records_pending_register_clears_it(
    client, auth_headers, stub_qiniu, db_session
):
    """upload-token 记账、register 成功后清账本。"""
    from app.models.pending_upload import PendingUpload

    def pending_count(key):
        s = db_session()
        try:
            return s.query(PendingUpload).filter_by(key=key).count()
        finally:
            s.close()

    tok = client.post("/api/fm/upload-token",
                      json={"path": "qiniu://", "name": "led.txt"},
                      headers=auth_headers).json()
    assert pending_count(tok["key"]) == 1

    stub_qiniu[tok["key"]] = (b"hello", "text/plain")
    client.post("/api/fm/register",
                json={"path": tok["path"], "key": tok["key"], "size": 5},
                headers=auth_headers)
    assert pending_count(tok["key"]) == 0


def test_register_overwrite_deletes_old_object(client, auth_headers, stub_qiniu):
    t1 = client.post("/api/fm/upload-token",
                     json={"path": "qiniu://", "name": "x.txt"},
                     headers=auth_headers).json()
    stub_qiniu[t1["key"]] = (b"old", "text/plain")
    client.post("/api/fm/register",
                json={"path": t1["path"], "key": t1["key"], "size": 3},
                headers=auth_headers)
    # 第二次上传同名文件 → 新 key，旧对象应被删除
    t2 = client.post("/api/fm/upload-token",
                     json={"path": "qiniu://", "name": "x.txt"},
                     headers=auth_headers).json()
    stub_qiniu[t2["key"]] = (b"new", "text/plain")
    client.post("/api/fm/register",
                json={"path": t2["path"], "key": t2["key"], "size": 3},
                headers=auth_headers)
    assert t1["key"] not in stub_qiniu
    assert t2["key"] in stub_qiniu


def test_rename_conflict(client, auth_headers, stub_qiniu):
    client.post("/api/fm/create-folder", json={"path": "qiniu://", "name": "a"},
                headers=auth_headers)
    client.post("/api/fm/create-folder", json={"path": "qiniu://", "name": "b"},
                headers=auth_headers)
    r = client.post("/api/fm/rename",
                    json={"path": "qiniu://", "item": "qiniu://a", "name": "b"},
                    headers=auth_headers)
    assert r.status_code == 409


def test_stats(client, auth_headers, stub_qiniu, monkeypatch):
    # 七牛统计 API 打桩为不可用 → used 回落到本地元数据求和
    monkeypatch.setattr(
        qiniu_client, "bucket_space",
        lambda: (_ for _ in ()).throw(RuntimeError("no stats")),
    )
    import app.api.fm as fm_mod
    monkeypatch.setitem(fm_mod._space_cache, "at", 0.0)

    assert client.get("/api/fm/stats").status_code == 401
    _upload(client, auth_headers, "qiniu://", "a.txt", b"12345")
    _upload(client, auth_headers, "qiniu://docs", "b.txt", b"123")
    r = client.get("/api/fm/stats", headers=auth_headers).json()
    assert r["used"] == r["used_local"] == 8
    assert r["used_remote"] is None
    assert r["files"] == 2 and r["dirs"] == 1
    assert r["quota"] > 0


def test_stats_prefers_remote_when_larger(client, auth_headers, stub_qiniu, monkeypatch):
    monkeypatch.setattr(qiniu_client, "bucket_space", lambda: 10**9)
    import app.api.fm as fm_mod
    monkeypatch.setitem(fm_mod._space_cache, "at", 0.0)
    _upload(client, auth_headers, "qiniu://", "a.txt", b"12345")
    r = client.get("/api/fm/stats", headers=auth_headers).json()
    assert r["used"] == 10**9 and r["used_local"] == 5
