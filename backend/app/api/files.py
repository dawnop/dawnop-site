"""文件接口（全部需登录）：上传（直传凭证 / 代理上传）、列表、私有 URL、删除。

文件本体存七牛私有空间；本地 SQLite 只存元信息用于列表与预览。
所有接口经 get_current_user 保护。
"""
import mimetypes
import uuid
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.config import settings
from app.core import qiniu_client
from app.deps import get_current_user, get_db
from app.models.file_object import FileObject
from app.models.user import User
from app.schemas.file import (
    FileListResponse,
    FileObjectOut,
    FileUrlResponse,
    RegisterFileRequest,
    UploadTokenRequest,
    UploadTokenResponse,
)

router = APIRouter(dependencies=[Depends(get_current_user)])


def _make_key(filename: str) -> str:
    """uuid 文件名避免覆盖；保留扩展名便于按类型预览。

    若 filename 含目录（文件夹导入，如 docs/sub/a.txt），把目录作为 key 前缀，
    使七牛 key 与本地一样体现文件夹层级。
    """
    p = PurePosixPath((filename or "").replace("\\", "/"))
    ext = p.suffix.lower()
    uid = f"{uuid.uuid4().hex}{ext}"
    parts = [seg for seg in p.parent.parts if seg not in ("", ".", "..", "/")]
    return f"{'/'.join(parts)}/{uid}" if parts else uid


def _guess_mime(filename: str, fallback: str | None = None) -> str:
    return mimetypes.guess_type(filename)[0] or fallback or "application/octet-stream"


def _get_or_404(db: Session, file_id: int) -> FileObject:
    obj = db.get(FileObject, file_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在")
    return obj


# ---------- 上传：方式一，前端直传（后端只签凭证 + 登记）----------


@router.post("/upload-token", response_model=UploadTokenResponse)
def create_upload_token(payload: UploadTokenRequest):
    key = _make_key(payload.filename)
    try:
        token = qiniu_client.upload_token(key)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    return UploadTokenResponse(
        token=token, key=key, expires=settings.qiniu_token_expires
    )


@router.post(
    "/register", response_model=FileObjectOut, status_code=status.HTTP_201_CREATED
)
def register_file(payload: RegisterFileRequest, db: Session = Depends(get_db)):
    size = payload.size
    content_type = payload.content_type
    # 未提供则向七牛 stat 补全
    if size is None or content_type is None:
        try:
            info = qiniu_client.stat(payload.key)
        except RuntimeError as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
        size = size if size is not None else int(info.get("fsize", 0))
        content_type = content_type or info.get("mimeType", "")

    obj = FileObject(
        key=payload.key,
        filename=payload.filename,
        content_type=content_type or _guess_mime(payload.filename),
        size=size or 0,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


# ---------- 上传：方式二，经后端代理上传（便于后台直接上传）----------


@router.post(
    "/upload", response_model=FileObjectOut, status_code=status.HTTP_201_CREATED
)
async def upload_file(
    file: UploadFile = File(...), db: Session = Depends(get_db)
):
    data = await file.read()
    key = _make_key(file.filename or "file")
    mime = file.content_type or _guess_mime(file.filename or "")
    try:
        qiniu_client.proxy_upload(key, data, mime)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    obj = FileObject(
        key=key,
        filename=file.filename or key,
        content_type=mime,
        size=len(data),
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


# ---------- 列表 / 预览 URL / 删除 ----------


@router.get("", response_model=FileListResponse)
def list_files(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(FileObject)
    total = query.count()
    items = (
        query.order_by(FileObject.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return FileListResponse(total=total, page=page, size=size, items=items)


@router.get("/{file_id}/url", response_model=FileUrlResponse)
def get_file_url(file_id: int, db: Session = Depends(get_db)):
    obj = _get_or_404(db, file_id)
    try:
        url = qiniu_client.private_url(obj.key)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    return FileUrlResponse(url=url, expires=settings.qiniu_token_expires)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(file_id: int, db: Session = Depends(get_db)):
    obj = _get_or_404(db, file_id)
    try:
        qiniu_client.delete(obj.key)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    db.delete(obj)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
