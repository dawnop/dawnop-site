"""取字节端点：sign（签名 URL）、content（后端代理）、preview/download（302 直连七牛）、search。

预览/下载默认 302 到七牛私有签名 URL（浏览器直连取字节，省后端流量）；图片给了
w/h 且为光栅图时改跳 imageView2 缩略图。文本预览走 /sign 直连或 /content 代理兜底。
"""

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.core import qiniu_client
from app.deps import get_current_user, get_current_user_flexible, get_db
from app.models.file_object import FileObject

from .paths import _basename, _parent_rel, _split
from .tree import _entry, _get

router = APIRouter()

# 服务端直取七牛用：忽略 http_proxy/all_proxy 等环境变量。
# 开发机常配本机代理，经它取七牛时好时坏（socks 变量还会因缺依赖直接报错）；
# 服务端直连没有 CORS 顾虑，不需要代理。
_plain_http = requests.Session()
_plain_http.trust_env = False


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
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"七牛取回失败：{r.status_code}"
        )
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
    mode: str = Query(
        "fit", pattern="^(fit|fill)$", description="fit=等比, fill=裁剪铺满"
    ),
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
