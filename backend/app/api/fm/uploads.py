"""上传端点：前端直传七牛（签凭证 + 登记）省后端流量，另有代理上传备用。"""
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core import qiniu_client
from app.deps import get_current_user, get_db
from app.models.file_object import FileObject
from app.models.pending_upload import PendingUpload
from app.schemas.fm import PathNameIn, RegisterIn

from .paths import _basename, _ext, _full, _guess_mime, _join, _split
from .tree import _ensure_dirs, _entry, _get

router = APIRouter()


# ---------------- 直传：签凭证 + 登记（省后端流量）----------------


@router.post("/upload-token")
def upload_token(
    body: PathNameIn,
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    """为前端直传七牛签发凭证：返回限定 key 的 token、目标 key/path、上传域名。

    SecretKey 不出后端；token 仅限本次 key、短时效。前端直传成功后调 /register 登记。
    """
    payload = body.model_dump()
    target = _split(payload.get("path"))
    name = (payload.get("name") or "file").replace("\\", "/").strip("/")
    rel = _join(target, name)
    ext = ("." + _ext(name)) if _ext(name) else ""
    key = f"{uuid.uuid4().hex}{ext}"
    try:
        token = qiniu_client.upload_token(key)
        host = qiniu_client.upload_host()
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    # 记账：本环境亲自签发的 key。/register 成功后清除；始终没登记的即孤儿。
    db.execute(
        sqlite_insert(PendingUpload)
        .values(key=key, path=_full(rel))
        .on_conflict_do_nothing(index_elements=["key"])
    )
    db.commit()
    return {"token": token, "key": key, "path": _full(rel), "up_host": host}


@router.post("/register")
def register(
    body: RegisterIn,
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    """前端直传七牛成功后登记元信息（path↔key）。

    登记前先 `stat` 校验对象确实已落到七牛，并以七牛返回的 fsize/mimeType 为准
    （不轻信前端上报的大小）。校验通过才建行，因此「已登记」严格等价于
    「七牛上真实存在」，不会产生悬空引用。登记成功后清掉待登记账本行。
    """
    payload = body.model_dump()
    rel = _split(payload.get("path"))
    key = payload.get("key")
    if not rel or not key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少 path / key")

    # 多一步验证：确认直传真的落到了七牛，并取权威大小/类型。
    try:
        info = qiniu_client.stat(key)
    except RuntimeError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "上传校验失败：对象未在七牛找到，请重试上传"
        )
    size = int(info.get("fsize") or payload.get("size") or 0)
    mime = (
        info.get("mimeType")
        or payload.get("content_type")
        or _guess_mime(_basename(rel))
    )

    _ensure_dirs(db, rel)
    existing = _get(db, rel)
    if existing and not existing.is_dir:
        # 覆盖：删除被替换的旧七牛对象，避免产生孤儿
        if existing.key and existing.key != key:
            try:
                qiniu_client.delete(existing.key)
            except RuntimeError:
                pass
        existing.key, existing.content_type, existing.size = key, mime, size
        obj = existing
    else:
        obj = FileObject(
            path=rel, is_dir=False, key=key, content_type=mime, size=size
        )
        db.add(obj)
    # 登记成功，从待登记账本移除该 key
    db.query(PendingUpload).filter(PendingUpload.key == key).delete()
    db.commit()
    db.refresh(obj)
    return _entry(obj)


# ---------------- upload（代理上传，备用 / 其他客户端）----------------


@router.post("/upload")
async def upload(
    path: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    target = _split(path)
    name = (file.filename or "file").replace("\\", "/").strip("/")
    rel = _join(target, name)
    data = await file.read()
    mime = file.content_type or _guess_mime(name)

    existing = _get(db, rel)
    key = existing.key if (existing and not existing.is_dir and existing.key) else uuid.uuid4().hex
    try:
        qiniu_client.proxy_upload(key, data, mime)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    _ensure_dirs(db, rel)
    if existing and not existing.is_dir:
        existing.key, existing.content_type, existing.size = key, mime, len(data)
        obj = existing
    else:
        obj = FileObject(
            path=rel, is_dir=False, key=key, content_type=mime, size=len(data)
        )
        db.add(obj)
    db.commit()
    db.refresh(obj)
    return _entry(obj)
