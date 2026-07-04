"""VueFinder 文件管理端点（/api/fm）。

匹配 VueFinder v4 内置 RemoteDriver 的线格式：
- list:        GET  ?path=qiniu://dir            → FsData
- upload:      POST multipart (file + path 字段)  → DirEntry
- delete:      POST {path, items:[{path,type}]}   → DeleteResult
- rename:      POST {path, item, name}            → FsData
- copy/move:   POST {sources, destination, path}  → FsData
- create-file/create-folder: POST {path, name}    → FsData
- preview:     GET  ?path=&token=                 → 文件字节（代理）
- download:    GET  ?path=&token=                 → 文件字节（attachment）
- search:      GET  ?path=&filter=&deep=&size=    → {files:[...]}
- save:        POST {path, content}               → {}

文件夹用 FileObject(is_dir=True) 行表示；七牛 key 为不透明 uuid，
因此 rename/move 只改 path（不动七牛对象），copy 才真正复制七牛对象。
私有空间：preview/download 由后端拉取签名 URL 再回传，避免跨域并隐藏签名。
"""
import mimetypes
import time
import uuid
from datetime import datetime

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
import requests
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.api.settings import get_setting
from app.core import qiniu_client
from app.deps import get_current_user, get_current_user_flexible, get_db
from app.models.file_object import FileObject
from app.models.pending_upload import PendingUpload

# 服务端直取七牛用：忽略 http_proxy/all_proxy 等环境变量。
# 开发机常配本机代理，经它取七牛时好时坏（socks 变量还会因缺依赖直接报错）；
# 服务端直连没有 CORS 顾虑，不需要代理。
_plain_http = requests.Session()
_plain_http.trust_env = False

router = APIRouter()

STORAGE = "qiniu"

# ---------------- 路径工具 ----------------


def _split(p: str | None) -> str:
    """去掉 storage 前缀，返回规范相对路径（根为空串）。"""
    if not p:
        return ""
    if "://" in p:
        p = p.split("://", 1)[1]
    return p.strip("/").strip()


def _full(rel: str) -> str:
    return f"{STORAGE}://{rel}" if rel else f"{STORAGE}://"


def _parent_rel(rel: str) -> str:
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def _basename(rel: str) -> str:
    return rel.rsplit("/", 1)[-1] if rel else ""


def _join(parent_rel: str, name: str) -> str:
    return f"{parent_rel}/{name}" if parent_rel else name


def _ext(name: str) -> str:
    i = name.rfind(".")
    return name[i + 1 :] if i > 0 else ""


def _guess_mime(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _ts(dt: datetime | None) -> int | None:
    return int(dt.timestamp()) if dt else None


# ---------------- DirEntry / 查询 ----------------


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
        rows = db.query(FileObject).filter(
            FileObject.path.like(f"{parent_rel}/%")
        ).all()
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


# ---------------- list ----------------


@router.get("")
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
    return {
        "used": max(db_used, remote or 0),
        "used_local": db_used,
        "used_remote": remote,
        "files": int(files),
        "dirs": int(dirs),
        "quota": int(get_setting(db, "storage_quota_gb")) * 1024**3,
    }


# ---------------- 直传：签凭证 + 登记（省后端流量）----------------


@router.post("/upload-token")
def upload_token(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    """为前端直传七牛签发凭证：返回限定 key 的 token、目标 key/path、上传域名。

    SecretKey 不出后端；token 仅限本次 key、短时效。前端直传成功后调 /register 登记。
    """
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
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    """前端直传七牛成功后登记元信息（path↔key）。

    登记前先 `stat` 校验对象确实已落到七牛，并以七牛返回的 fsize/mimeType 为准
    （不轻信前端上报的大小）。校验通过才建行，因此「已登记」严格等价于
    「七牛上真实存在」，不会产生悬空引用。登记成功后清掉待登记账本行。
    """
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


def _reparent(db: Session, old_rel: str, new_rel: str) -> None:
    """把 old_rel（及其后代）的 path 前缀整体改为 new_rel。"""
    for o in _subtree(db, old_rel):
        o.path = new_rel + o.path[len(old_rel):]


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


# ---------------- preview / download（302 重定向到签名 URL，不经后端中转）----------------


def _file_or_404(db: Session, path: str | None) -> FileObject:
    o = _get(db, _split(path))
    if o is None or o.is_dir or not o.key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在")
    return o


def _redirect(url: str) -> RedirectResponse:
    # 302：浏览器随后直接从七牛取字节，后端只回了这个跳转
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


# 七牛 imageView2 能处理的光栅格式；svg（矢量）等原样返回，不套缩略图
_THUMBABLE = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/bmp",
    "image/x-ms-bmp",
    "image/tiff",
}

# 原图已足够小（字节数）就不生成缩略图：省一次图片处理、且缩略图未必更小
_THUMB_MIN_BYTES = 50 * 1024


def _thumb_fop(
    content_type: str | None,
    size: int | None,
    w: int | None,
    h: int | None,
    mode: str,
) -> str | None:
    """按目标尺寸构造 imageView2 指令；非光栅图/未给尺寸/原图已很小时返回 None（走原图）。"""
    if not w and not h:
        return None
    if (content_type or "") not in _THUMBABLE:
        return None
    if (size or 0) <= _THUMB_MIN_BYTES:
        return None
    code = "1" if mode == "fill" else "2"  # 1=裁剪铺满, 2=等比缩放
    parts = [f"imageView2/{code}"]
    if w:
        parts.append(f"w/{w}")
    if h:
        parts.append(f"h/{h}")
    parts.append("format/webp")
    parts.append("q/82")
    return "/".join(parts)


@router.get("/sign")
def sign(
    path: str = Query(...),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    """返回文件的私有签名 URL（供前端**直连七牛**取文本内容，避免重定向导致 Origin:null）。"""
    o = _file_or_404(db, path)
    try:
        return {"url": qiniu_client.private_url(o.key)}
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))


@router.get("/content")
def content(
    path: str = Query(...),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    """后端代理回传文件字节（前端直连七牛被 CORS 拦时的兜底）。

    直连 fetch 依赖存储域名的 CORS 配置；生产自定义域名已配好，但本地开发走
    七牛测试域名时没有（也无法配），文本预览/带进度下载会被浏览器拦下。
    此端点由后端拉取后流式回传，带 Content-Length 供前端算进度。
    """
    o = _file_or_404(db, path)
    try:
        url = qiniu_client.private_url(o.key)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    # 要求 identity 编码：iter_content 会自动解压 gzip，解压后的字节数与上游
    # Content-Length 不符会让 uvicorn 报「shorter than Content-Length」
    r = _plain_http.get(
        url, stream=True, timeout=30, headers={"Accept-Encoding": "identity"}
    )
    if r.status_code != 200:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"七牛取回失败：{r.status_code}")
    headers = {}
    if r.headers.get("Content-Length") and not r.headers.get("Content-Encoding"):
        headers["Content-Length"] = r.headers["Content-Length"]
    return StreamingResponse(
        r.iter_content(64 * 1024),
        media_type=o.content_type or "application/octet-stream",
        headers=headers,
    )


@router.get("/preview")
def preview(
    path: str = Query(...),
    w: int | None = Query(None, ge=1, le=2000, description="缩略图目标宽度"),
    h: int | None = Query(None, ge=1, le=2000, description="缩略图目标高度"),
    mode: str = Query("fit", pattern="^(fit|fill)$", description="fit=等比, fill=裁剪铺满"),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user_flexible),
):
    """图片/文件预览。给了 w/h 且为光栅图时，302 到七牛 imageView2 缩略图（省流量）。"""
    o = _file_or_404(db, path)
    fop = _thumb_fop(o.content_type, o.size, w, h, mode)
    try:
        return _redirect(qiniu_client.private_url(o.key, fop=fop))
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))


@router.get("/download")
def download(
    path: str = Query(...),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user_flexible),
):
    o = _file_or_404(db, path)
    try:
        return _redirect(qiniu_client.private_url(o.key, attname=_basename(o.path)))
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))


# ---------------- search ----------------


@router.get("/search")
def search(
    path: str | None = Query(None),
    filter: str = Query(""),
    deep: str | None = Query(None),
    size: str | None = Query(None),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    base = _split(path)
    needle = filter.lower().strip()
    if base:
        rows = db.query(FileObject).filter(FileObject.path.like(f"{base}/%")).all()
    else:
        rows = db.query(FileObject).all()
    if not deep:
        rows = [o for o in rows if _parent_rel(o.path) == base]
    if needle:
        rows = [o for o in rows if needle in _basename(o.path).lower()]
    rows.sort(key=lambda o: (not o.is_dir, o.path.lower()))
    return {"files": [_entry(o) for o in rows]}


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
