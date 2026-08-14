"""#267：改名 / 移动只改 path，不推进 updated_at（整棵子树都不推进）。

同一族的第五处（前四处见 test_view_counter.py / test_page_updated_at.py /
test_noop_put_updated_at.py），规矩仍是那一条：**只有对该行的显式编辑才推进
`updated_at`；由别处的操作顺带引起的字段变更不推进。**

这次两个后端本来是一致的——都在移动时把整棵子树刷成此刻（Dawn 那行注释明说是在
对齐 FastAPI 的 onupdate），所以不是分歧，是两边一致地违反了规矩。判它不推进的
三条理由写在 `app/api/fm/tree.py:_reparent` 的 docstring 里（两个出口都把
updated_at 当「内容最后修改时间」读 / POSIX mv 不动 mtime / 七牛对象根本没动）。

同这一族的其他判词：种一个明显旧的时间戳再比对**库里存的原始字符串**。SQLite 的
时间戳只到秒，用「刚建好」的值会让刷写过和没刷写过长得一模一样。

最后两条是反向判词：真改内容的路径（/save 文本编辑、/register 覆盖上传）**仍然**
推进。它们防的是有人日后拿「把 FileObject 的 onupdate 删掉」来「修」这个问题。
"""

import base64
from datetime import datetime

import pytest
from app.api.fm.tree import _reparent, _subtree
from app.core import qiniu_client
from app.models.file_object import FileObject
from sqlalchemy import text
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME

OLD = datetime(2000, 1, 2, 3, 4, 5)


@pytest.fixture
def stub_qiniu(monkeypatch):
    """只给反向判词用：改名/移动压根不碰七牛。"""
    store: dict[str, tuple[bytes, str]] = {}

    def fake_proxy_upload(key, data, mime=None):
        store[key] = (data, mime or "application/octet-stream")
        return {"key": key, "hash": "h"}

    def fake_delete(key):
        store.pop(key, None)

    def fake_stat(key):
        if key not in store:
            raise RuntimeError("no such object")
        data, mime = store[key]
        return {"fsize": len(data), "mimeType": mime, "hash": "h"}

    monkeypatch.setattr(qiniu_client, "proxy_upload", fake_proxy_upload)
    monkeypatch.setattr(qiniu_client, "delete", fake_delete)
    monkeypatch.setattr(qiniu_client, "stat", fake_stat)
    return store


def _seed(db_session, rows):
    """直接入库建行（INSERT 不触发 onupdate），时间戳一律种成 OLD。"""
    session = db_session()
    for path, is_dir, key in rows:
        session.add(
            FileObject(
                path=path,
                is_dir=is_dir,
                key=key,
                content_type="" if is_dir else "text/plain",
                size=0 if is_dir else 3,
                created_at=OLD,
                updated_at=OLD,
            )
        )
    session.commit()
    session.close()


def _stamps(db_session):
    """{path: updated_at 原始字符串}，供逐字节比对。"""
    session = db_session()
    try:
        return {
            row[0]: row[1]
            for row in session.execute(text("SELECT path, updated_at FROM files"))
        }
    finally:
        session.close()


def _stamp_of(db_session, path):
    session = db_session()
    try:
        return session.execute(
            text("SELECT updated_at FROM files WHERE path = :p"), {"p": path}
        ).scalar()
    finally:
        session.close()


STALE = "2000-01-02 03:04:05.000000"


def test_seeded_stamp_is_distinctly_old(db_session):
    """判词的前提：种进去的就是这个字符串。

    比对的是原始字符串，所以「种下去的形状」本身要先钉住——否则某天 DateTime 的
    存储格式一变，下面几条会拿两个都不是 OLD 的值比出「相等」而假绿。
    """
    _seed(db_session, [("a.txt", False, "k-a")])
    assert _stamp_of(db_session, "a.txt") == STALE


def test_renaming_a_file_does_not_advance_its_updated_at(
    client, auth_headers, db_session
):
    _seed(db_session, [("a.txt", False, "k-a")])

    resp = client.post(
        "/api/fm/rename",
        json={"path": "qiniu://", "item": "qiniu://a.txt", "name": "b.txt"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    # path 真的改了（不然「时间戳没动」是因为什么都没做）
    assert _stamp_of(db_session, "a.txt") is None
    assert _stamp_of(db_session, "b.txt") == STALE


def test_renaming_a_directory_leaves_every_descendant_alone(
    client, auth_headers, db_session
):
    _seed(
        db_session,
        [
            ("d", True, None),
            ("d/x.txt", False, "k-x"),
            ("d/sub", True, None),
            ("d/sub/y.txt", False, "k-y"),
        ],
    )

    resp = client.post(
        "/api/fm/rename",
        json={"path": "qiniu://", "item": "qiniu://d", "name": "e"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert _stamps(db_session) == {
        "e": STALE,
        "e/x.txt": STALE,
        "e/sub": STALE,
        "e/sub/y.txt": STALE,
    }


def test_moving_a_directory_leaves_every_descendant_alone(
    client, auth_headers, db_session
):
    _seed(
        db_session,
        [
            ("box", True, None),
            ("d", True, None),
            ("d/x.txt", False, "k-x"),
            ("d/sub", True, None),
            ("d/sub/y.txt", False, "k-y"),
        ],
    )

    resp = client.post(
        "/api/fm/move",
        json={
            "path": "qiniu://",
            "destination": "qiniu://box",
            "sources": ["qiniu://d"],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert _stamps(db_session) == {
        "box": STALE,
        "box/d": STALE,
        "box/d/x.txt": STALE,
        "box/d/sub": STALE,
        "box/d/sub/y.txt": STALE,
    }


def test_moving_several_sources_in_one_request_leaves_them_all_alone(
    client, auth_headers, db_session
):
    """一次请求搬多棵子树：`_reparent` 被连着调用好几次。

    这一条顺带钉住 Core update 的收尾 `expire_all()`：绕过 identity map 之后，
    第二次 `_subtree()` 必须拿到库里的现值，不能是内存里那份旧 path。
    """
    _seed(
        db_session,
        [
            ("box", True, None),
            ("a.txt", False, "k-a"),
            ("d", True, None),
            ("d/x.txt", False, "k-x"),
        ],
    )

    resp = client.post(
        "/api/fm/move",
        json={
            "path": "qiniu://",
            "destination": "qiniu://box",
            "sources": ["qiniu://a.txt", "qiniu://d"],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert _stamps(db_session) == {
        "box": STALE,
        "box/a.txt": STALE,
        "box/d": STALE,
        "box/d/x.txt": STALE,
    }


def test_webdav_move_leaves_updated_at_alone(client, db_session):
    """WebDAV 的 MOVE 走同一个 `_reparent`，`getlastmodified` 读的就是这一列。"""
    raw = base64.b64encode(f"{ADMIN_USERNAME}:{ADMIN_PASSWORD}".encode()).decode()
    auth = {"Authorization": f"Basic {raw}"}
    _seed(db_session, [("d", True, None), ("d/x.txt", False, "k-x")])

    resp = client.request(
        "MOVE", "/dav/d", headers={**auth, "Destination": "/dav/moved"}
    )
    assert resp.status_code == 201
    assert _stamps(db_session) == {"moved": STALE, "moved/x.txt": STALE}


def test_reparent_leaves_no_stale_path_in_the_session(db_session):
    """白盒：`_reparent` 返回后，session 里的对象必须和库一致。

    Core `update()` 绕过 identity map——被改的行在内存里仍留着旧 path，而 ORM
    属性赋值不会有这个问题。这条钉的是 `_reparent` 每改一行后的那句 `expire(o)`。
    走函数而不走路由，是因为路由都在 `db.commit()` 之后才读（expire_on_commit
    会把这一层盖住），黑盒判词照不出这个差别。
    """
    _seed(db_session, [("d", True, None), ("d/x.txt", False, "k-x")])
    session = db_session()
    try:
        rows = _subtree(session, "d")
        assert sorted(o.path for o in rows) == ["d", "d/x.txt"]
        _reparent(session, "d", "e")
        assert sorted(o.path for o in rows) == ["e", "e/x.txt"]
    finally:
        session.close()


# ---- 反向判词：真改内容的路径仍然推进 ----


def test_saving_text_still_advances_updated_at(
    client, auth_headers, db_session, stub_qiniu
):
    _seed(db_session, [("a.txt", False, "k-a")])
    stub_qiniu["k-a"] = (b"old", "text/plain")

    resp = client.post(
        "/api/fm/save",
        json={"path": "qiniu://a.txt", "content": "new body"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert _stamp_of(db_session, "a.txt") != STALE


def test_overwriting_by_register_still_advances_updated_at(
    client, auth_headers, db_session, stub_qiniu
):
    _seed(db_session, [("a.txt", False, "k-a")])
    stub_qiniu["k-new"] = (b"fresh bytes", "text/plain")

    resp = client.post(
        "/api/fm/register",
        json={"path": "qiniu://a.txt", "key": "k-new"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert _stamp_of(db_session, "a.txt") != STALE
