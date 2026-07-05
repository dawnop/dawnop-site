"""文件树增删改端点：列目录、用量统计、删除、重命名、移动、复制、建目录/文件、文本保存。"""
import time
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.settings import get_setting
from app.core import qiniu_client
from app.deps import get_current_user, get_db
from app.models.file_object import FileObject

from .paths import _basename, _ext, _full, _guess_mime, _join, _parent_rel, _split
from .tree import (
    _conflict_if_exists,
    _ensure_dirs,
    _entry,
    _fs_data,
    _get,
    _reparent,
    _subtree,
)

router = APIRouter()


# ---------------- list ----------------
# 注意：列目录挂在空路径 "" 上，不能经 include_router（空前缀+空路径会报错），
# 故此处只定义函数、由包 __init__ 直接注册到聚合 router。


def list_dir(
    path: str | None = Query(None),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    return _fs_data(db, _split(path))


# 七牛空间统计缓存：统计 API 有小时级延迟且没必要每次列目录都打
_space_cache: dict = {"at": 0.0, "value": None}


@router.get("/stats")
def stats(
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    """存储用量统计（文件管理侧栏用量条）。

    used 取「本地元数据求和」与「七牛统计 API」的较大者：本地求和实时但
    看不到孤儿对象；七牛统计权威但有小时级延迟。quota 来自全局配置，仅展示。
    """
    db_used = int(
        db.query(func.coalesce(func.sum(FileObject.size), 0))
        .filter(FileObject.is_dir.is_(False))
        .scalar()
    )
    now = time.time()
    if now - _space_cache["at"] > 600:
        try:
            _space_cache["value"] = qiniu_client.bucket_space()
        except RuntimeError:
            _space_cache["value"] = None  # 未配置/失败则只用本地求和
        _space_cache["at"] = now
    remote = _space_cache["value"]
    files = db.query(func.count()).filter(FileObject.is_dir.is_(False)).scalar()
    dirs = db.query(func.count()).filter(FileObject.is_dir.is_(True)).scalar()
    # 配额与监控页对齐：优先用七牛存储资源包容量，无包则回落全局设置（respack 自带 TTL 缓存）
    quota = None
    try:
        sp = qiniu_client.respack_summary().get("storage")
        if sp and sp.get("capacity"):
            quota = int(sp["capacity"])
    except Exception:
        pass
    if quota is None:
        quota = int(get_setting(db, "storage_quota_gb")) * 1024**3
    return {
        "used": max(db_used, remote or 0),
        "used_local": db_used,
        "used_remote": remote,
        "files": int(files),
        "dirs": int(dirs),
        "quota": quota,
    }


# ---------------- delete ----------------


@router.post("/delete")
def delete(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    cur = _split(payload.get("path"))
    items = payload.get("items") or []
    deleted: list[dict] = []
    for it in items:
        rel = _split(it.get("path"))
        if not rel:
            continue
        for o in _subtree(db, rel):
            if not o.is_dir and o.key:
                try:
                    qiniu_client.delete(o.key)
                except RuntimeError as e:
                    raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
            deleted.append(_entry(o))
            db.delete(o)
    db.commit()
    return _fs_data(db, cur, {"deleted": deleted})


# ---------------- rename ----------------


@router.post("/rename")
def rename(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    cur = _split(payload.get("path"))
    old = _split(payload.get("item"))
    new_name = (payload.get("name") or "").strip().strip("/")
    if not old or not new_name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少 item / name")
    new_rel = _join(_parent_rel(old), new_name)
    if new_rel != old:
        _conflict_if_exists(db, new_rel)
        _reparent(db, old, new_rel)
        db.commit()
    return _fs_data(db, cur)


# ---------------- move ----------------


@router.post("/move")
def move(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    cur = _split(payload.get("path"))
    dest = _split(payload.get("destination"))
    for src in payload.get("sources") or []:
        src_rel = _split(src)
        if not src_rel:
            continue
        new_rel = _join(dest, _basename(src_rel))
        if new_rel == src_rel or new_rel.startswith(src_rel + "/"):
            continue  # 不能移动到自身或子目录
        _conflict_if_exists(db, new_rel)
        _ensure_dirs(db, new_rel)
        _reparent(db, src_rel, new_rel)
    db.commit()
    return _fs_data(db, cur)


# ---------------- copy ----------------


@router.post("/copy")
def copy(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    cur = _split(payload.get("path"))
    dest = _split(payload.get("destination"))
    for src in payload.get("sources") or []:
        src_rel = _split(src)
        if not src_rel:
            continue
        new_rel = _join(dest, _basename(src_rel))
        if new_rel.startswith(src_rel + "/"):
            continue
        _conflict_if_exists(db, new_rel)
        _ensure_dirs(db, new_rel)
        for o in _subtree(db, src_rel):
            dst_path = new_rel + o.path[len(src_rel):]
            if o.is_dir:
                db.add(FileObject(path=dst_path, is_dir=True, key=None, size=0))
            else:
                new_key = uuid.uuid4().hex
                try:
                    qiniu_client.copy(o.key, new_key)
                except RuntimeError as e:
                    raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
                db.add(FileObject(
                    path=dst_path, is_dir=False, key=new_key,
                    content_type=o.content_type, size=o.size,
                ))
    db.commit()
    return _fs_data(db, cur)


# ---------------- create-folder / create-file ----------------


@router.post("/create-folder")
def create_folder(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    cur = _split(payload.get("path"))
    name = (payload.get("name") or "").strip().strip("/")
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少 name")
    rel = _join(cur, name)
    _conflict_if_exists(db, rel)
    _ensure_dirs(db, rel)
    db.add(FileObject(path=rel, is_dir=True, key=None, size=0))
    db.commit()
    return _fs_data(db, cur)


@router.post("/create-file")
def create_file(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    cur = _split(payload.get("path"))
    name = (payload.get("name") or "").strip().strip("/")
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少 name")
    rel = _join(cur, name)
    _conflict_if_exists(db, rel)
    mime = _guess_mime(name)
    key = uuid.uuid4().hex
    try:
        qiniu_client.proxy_upload(key, b"", mime)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    _ensure_dirs(db, rel)
    db.add(FileObject(path=rel, is_dir=False, key=key, content_type=mime, size=0))
    db.commit()
    return _fs_data(db, cur)


# ---------------- save（文本编辑）----------------


@router.post("/save")
def save(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    rel = _split(payload.get("path"))
    content = (payload.get("content") or "").encode("utf-8")
    o = _get(db, rel)
    mime = (o.content_type if o else None) or _guess_mime(rel) or "text/plain"
    # 写到新 key、再删旧 key（与 /register 覆盖同策略）：七牛 CDN 按 URL 缓存
    # （max-age 30 天），同 key 覆盖后读回的是旧缓存，保存会看起来没生效。
    ext = ("." + _ext(rel)) if _ext(rel) else ""
    key = f"{uuid.uuid4().hex}{ext}"
    try:
        qiniu_client.proxy_upload(key, content, mime)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    old_key = o.key if o else None
    if o is None:
        _ensure_dirs(db, rel)
        o = FileObject(path=rel, is_dir=False, key=key, content_type=mime)
        db.add(o)
    o.key, o.size, o.content_type = key, len(content), mime
    db.commit()
    if old_key and old_key != key:
        try:
            qiniu_client.delete(old_key)
        except RuntimeError:
            pass  # 旧对象清理失败不影响保存结果
    return {"path": _full(rel)}
