"""FileObject 虚拟文件树：子项/子树查询、DirEntry 序列化、目录补建、改父路径。

七牛 key 为不透明 uuid：rename/move 只改 path（不动七牛对象，见 _reparent），
copy 才真正复制七牛对象（在路由层处理）。文件夹用 FileObject(is_dir=True) 行表示。
"""

from fastapi import HTTPException, status
from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.file_object import FileObject

from .paths import STORAGE, _basename, _ext, _full, _join, _parent_rel, _ts


def _entry(o: FileObject) -> dict:
    rel = o.path
    return {
        "storage": STORAGE,
        "dir": _full(_parent_rel(rel)),
        "basename": _basename(rel),
        "extension": "" if o.is_dir else _ext(rel),
        "path": _full(rel),
        "type": "dir" if o.is_dir else "file",
        "file_size": None if o.is_dir else o.size,
        "last_modified": _ts(o.updated_at),
        "mime_type": None if o.is_dir else (o.content_type or None),
        "visibility": "public",
    }


def _children(db: Session, parent_rel: str) -> list[FileObject]:
    """直接子项：path 的父目录恰为 parent_rel。"""
    if parent_rel:
        rows = (
            db.query(FileObject).filter(FileObject.path.like(f"{parent_rel}/%")).all()
        )
    else:
        rows = db.query(FileObject).all()
    kids = [o for o in rows if _parent_rel(o.path) == parent_rel]
    kids.sort(key=lambda o: (not o.is_dir, o.path.lower()))
    return kids


def _subtree(db: Session, rel: str) -> list[FileObject]:
    """rel 自身 + 其全部后代。"""
    return (
        db.query(FileObject)
        .filter((FileObject.path == rel) | (FileObject.path.like(f"{rel}/%")))
        .all()
    )


def _get(db: Session, rel: str) -> FileObject | None:
    return db.query(FileObject).filter(FileObject.path == rel).first()


def _fs_data(db: Session, dir_rel: str, extra: dict | None = None) -> dict:
    data = {
        "storages": [STORAGE],
        "dirname": _full(dir_rel),
        "files": [_entry(o) for o in _children(db, dir_rel)],
        "read_only": False,
    }
    if extra:
        data.update(extra)
    return data


def _ensure_dirs(db: Session, rel: str) -> None:
    """为 rel 的每个祖先目录补建文件夹行（不含 rel 自身）。

    用 INSERT OR IGNORE：前端并行直传同一新目录下的多个文件时，多个 /register
    会并发补建同一祖先目录，普通 INSERT 会让后到者撞 UNIQUE(path) 直接 500。
    """
    parent = _parent_rel(rel)
    parts = parent.split("/") if parent else []
    acc = ""
    for seg in parts:
        acc = _join(acc, seg)
        if _get(db, acc) is None:
            db.execute(
                sqlite_insert(FileObject)
                .values(path=acc, is_dir=True, key=None, size=0)
                .on_conflict_do_nothing(index_elements=["path"])
            )
    if parts:
        db.flush()


def _conflict_if_exists(db: Session, rel: str) -> None:
    if _get(db, rel) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"已存在同名项：{_basename(rel)}")


def _reparent(db: Session, old_rel: str, new_rel: str) -> None:
    """把 old_rel（及其后代）的 path 前缀整体改为 new_rel，**不推进 updated_at**。

    改名/移动不是对这些行的编辑（#267），三条理由：
      1. `updated_at` 在两个出口上都被当成「内容最后修改时间」读——列目录的
         `last_modified`（见上面 `_entry`）与 WebDAV 的 `getlastmodified`
         （RFC 4918 说的就是内容的最后修改时间）。
      2. POSIX `mv` 不改文件的 mtime，挂载端（Finder / rclone / Mountain Duck）
         就是按这个预期来的。
      3. 七牛那边的对象根本没动——改名只改 path、不动 key，是本仓刻意的设计。
         字节没变却报「刚刚改过」，是假的。

    写法上必须走 Core `update()` 并把 `updated_at` 显式放进 SET：ORM 属性上的
    自赋值（`o.updated_at = o.updated_at`）不会进 SET 子句（SQLAlchemy 逐列比对
    认为它没变），于是 `FileObject.updated_at` 的 onupdate 照样赢。SET 里写的是
    `updated_at = files.updated_at`（SQL 层自赋值），故存的字符串一个字节不变。

    Core update 绕过 identity map，被改的行在内存里会留着旧 path，故逐行
    `db.expire(o)`：`move` 一次请求可以连着改好几棵子树，下一次 `_subtree()` 拿到
    的必须是库里的现值。调用方在 `db.commit()` 之后才读（`_fs_data`），那时
    expire_on_commit 也会再刷一遍，这里是提前一步、把循环内部也盖住。
    逐行而不是 `expire_all()`：过期会把该对象上尚未 flush 的改动一并丢掉，而这里
    只有这些行被 Core update 绕过去了，没有理由去动 session 里别人的东西。
    """
    for o in _subtree(db, old_rel):
        db.execute(
            update(FileObject)
            .where(FileObject.id == o.id)
            .values(
                path=new_rel + o.path[len(old_rel) :],
                updated_at=FileObject.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        db.expire(o)
