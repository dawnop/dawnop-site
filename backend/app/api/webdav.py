"""可读写 WebDAV 适配层：把 FileObject 的 path↔key 树以标准 WebDAV 暴露，
供 Finder / RaiDrive / rclone / Mountain Duck 等挂载浏览、预览、下载、编辑。

读方法：OPTIONS / PROPFIND / HEAD / GET。
写方法：PUT（新建/覆盖文件）/ DELETE / MKCOL（建目录）/ MOVE（移动/重命名）/
COPY（复制）/ LOCK / UNLOCK。广播 DAV class 1,2（含 LOCK，macOS Finder 据此把
挂载当**可写**；否则只读挂载）。写操作复用 fm 的原语（proxy_upload/delete/copy/
_reparent/_ensure_dirs），语义与网页文件管理器一致：改名/移动只改 path 不动七牛
对象，复制才真正复制；覆盖走「写新 key + 删旧 key」避七牛 CDN 缓存。

LOCK 是**假锁**（单管理员场景无并发写冲突）：总是授予、回一个 opaquelocktoken，
不真正追踪——只为让 macOS webdavfs 认为可写。

**取字节的两条路**（与网页端 302 直连的取舍不同，见 CLAUDE.md）：
  - UA 命中「会跟随 302」的成熟客户端（rclone/Cyberduck/Mountain Duck/RaiDrive）
    → 302 到七牛签名 URL，浏览器/客户端直连七牛，省后端流量；
  - 其余（含 macOS webdavfs、Windows mini-redirector 这类系统内核挂载器，
    跨域 302 不可靠）→ 后端代理字节，并**透传 Range**（QuickLook/流媒体/续传要分段读）。

鉴权走 HTTP Basic（客户端普遍支持），校验管理员账号；bcrypt 较慢，用带 TTL 的
内存缓存挡住挂载时的高频请求。生产务必 HTTPS（Basic 明文）。
"""
import base64
import hashlib
import os
import tempfile
import time
import uuid
from email.utils import formatdate, parsedate_to_datetime
from urllib.parse import quote, unquote, urlparse
from xml.sax.saxutils import escape

import requests
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.fm import (
    _basename,
    _children,
    _ensure_dirs,
    _ext,
    _get,
    _guess_mime,
    _parent_rel,
    _reparent,
    _subtree,
    _ts,
)
from app.core import qiniu_client
from app.core.security import verify_password
from app.deps import get_db
from app.models.file_object import FileObject
from app.models.user import User

router = APIRouter()

DAV_PREFIX = "/dav"

# 服务端直取七牛：忽略本机 http_proxy 等（同 fm._plain_http）
_plain_http = requests.Session()
_plain_http.trust_env = False

# UA（小写）子串命中即认为「会跟随 302」，可直连七牛省后端流量
_REDIRECT_CLIENTS = ("rclone", "cyberduck", "mountain duck", "raidrive", "webdavlib")

# Basic 认证结果缓存：sha256("user:pass") -> 过期时间戳（避免每个请求都跑 bcrypt）
_auth_cache: dict[str, float] = {}
_AUTH_TTL = 300.0


def _dav_401() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="需要认证",
        headers={"WWW-Authenticate": 'Basic realm="dawnop-webdav"'},
    )


def _dav_user(request: Request, db: Session) -> User:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        raise _dav_401()
    try:
        raw = base64.b64decode(header[6:]).decode("utf-8")
    except Exception:
        raise _dav_401()
    username, sep, password = raw.partition(":")
    if not sep:
        raise _dav_401()

    ckey = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    now = time.time()
    exp = _auth_cache.get(ckey)
    if exp and exp > now:
        user = db.query(User).filter(User.username == username).first()
        if user:
            return user
    user = db.query(User).filter(User.username == username).first()
    if user is None or not verify_password(password, user.password_hash):
        raise _dav_401()
    _auth_cache[ckey] = now + _AUTH_TTL
    return user


def _follows_redirect(request: Request) -> bool:
    ua = request.headers.get("User-Agent", "").lower()
    return any(tag in ua for tag in _REDIRECT_CLIENTS)


def _dav_prefix(request: Request) -> str:
    """外部可见的 WebDAV 根前缀，决定 PROPFIND href 与 Destination 解析。

    默认 `/dav`（主域 `dawnop.com/dav`，路径挂载）。子域名 vhost
    （`dav.dawnop.com`）里 nginx 把 `/` 反代到后端 `/dav/`，并传
    `X-Dav-Prefix: /` 表示「这里 WebDAV 根 = 域名根」，归一化为空串，
    于是 href 出 `/foo.txt` 而非 `/dav/foo.txt`（避免子域名下双前缀 404）。
    """
    raw = request.headers.get("X-Dav-Prefix", DAV_PREFIX)
    p = "/" + raw.strip("/")
    return "" if p == "/" else p


# ---------------- PROPFIND XML ----------------


def _href(prefix: str, rel: str, is_dir: bool) -> str:
    segs = rel.split("/") if rel else []
    path = "/".join(quote(s) for s in segs)
    if not path:
        href = prefix + "/" if prefix else "/"  # 根
    else:
        href = f"{prefix}/{path}"
    if is_dir and not href.endswith("/"):
        href += "/"
    return href


def _etag(key: str | None) -> str | None:
    """强 ETag = 七牛 key。key 是内容不可变的 uuid（编辑/覆盖都换新 key），
    故同一 key 的字节永不变，用它当校验符最稳；客户端 If-None-Match 命中即 304。"""
    return f'"{key}"' if key else None


def _not_modified(request: Request, etag: str | None, mtime_ts: int | None) -> bool:
    """按 RFC 7232 判断客户端缓存是否仍新：
    If-None-Match 优先（在则忽略 If-Modified-Since），命中 ETag 或 `*` → 未改；
    否则退到 If-Modified-Since 比 Last-Modified。命中即可回 304、免传字节。"""
    inm = request.headers.get("If-None-Match")
    if inm is not None:
        inm = inm.strip()
        if inm == "*":
            return True
        if etag:
            for tok in inm.split(","):
                tok = tok.strip()
                if tok == etag or tok.removeprefix("W/") == etag:
                    return True
        return False
    ims = request.headers.get("If-Modified-Since")
    if ims and mtime_ts is not None:
        try:
            if mtime_ts <= int(parsedate_to_datetime(ims).timestamp()):
                return True
        except (TypeError, ValueError):
            pass
    return False


def _http_date(ts: int | None) -> str:
    return formatdate(ts if ts is not None else time.time(), usegmt=True)


def _iso_date(ts: int | None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts if ts else time.time()))


def _response_xml(
    prefix: str,
    rel: str,
    name: str,
    is_dir: bool,
    size: int,
    mime: str,
    mtime: int | None,
    ctime: int | None,
    etag: str | None = None,
) -> str:
    if is_dir:
        typ = "<D:collection/>"
        extra = ""
    else:
        typ = ""
        etag_xml = f"<D:getetag>{escape(etag)}</D:getetag>" if etag else ""
        extra = (
            f"<D:getcontentlength>{size}</D:getcontentlength>"
            f"<D:getcontenttype>{escape(mime or 'application/octet-stream')}</D:getcontenttype>"
            f"{etag_xml}"
        )
    return (
        "<D:response>"
        f"<D:href>{escape(_href(prefix, rel, is_dir))}</D:href>"
        "<D:propstat><D:prop>"
        f"<D:displayname>{escape(name)}</D:displayname>"
        f"<D:resourcetype>{typ}</D:resourcetype>"
        f"<D:getlastmodified>{_http_date(mtime)}</D:getlastmodified>"
        f"<D:creationdate>{_iso_date(ctime)}</D:creationdate>"
        f"{extra}"
        "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>"
        "</D:response>"
    )


def _entry_xml_from_obj(prefix: str, o) -> str:
    name = o.path.rsplit("/", 1)[-1] if o.path else ""
    return _response_xml(
        prefix, o.path, name, o.is_dir, o.size or 0, o.content_type or "",
        _ts(o.updated_at), _ts(o.created_at),
        etag=None if o.is_dir else _etag(o.key),
    )


def _propfind(request: Request, db: Session, rel: str) -> Response:
    prefix = _dav_prefix(request)
    depth = request.headers.get("Depth", "1")
    parts = []

    if rel == "":
        parts.append(_response_xml(prefix, "", "", True, 0, "", None, None))
        target_is_dir = True
    else:
        o = _get(db, rel)
        if o is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "资源不存在")
        parts.append(_entry_xml_from_obj(prefix, o))
        target_is_dir = o.is_dir

    if depth != "0" and target_is_dir:
        for kid in _children(db, rel):
            parts.append(_entry_xml_from_obj(prefix, kid))

    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<D:multistatus xmlns:D="DAV:">' + "".join(parts) + "</D:multistatus>"
    )
    return Response(
        content=body,
        status_code=207,
        media_type='application/xml; charset="utf-8"',
    )


# ---------------- GET / HEAD ----------------


def _get_or_head(request: Request, db: Session, rel: str, head: bool) -> Response:
    if rel == "":
        raise HTTPException(status.HTTP_405_METHOD_NOT_ALLOWED, "不能下载目录")
    o = _get(db, rel)
    if o is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "资源不存在")
    if o.is_dir or not o.key:
        raise HTTPException(status.HTTP_405_METHOD_NOT_ALLOWED, "不能下载目录")

    mime = o.content_type or "application/octet-stream"
    etag = _etag(o.key)
    last_mod = _http_date(_ts(o.updated_at))

    # 条件请求：客户端手里的副本仍是最新 → 304，省掉整次下载。
    # ETag 强校验（key 不可变）优先；退一步用 If-Modified-Since 比 Last-Modified。
    if _not_modified(request, etag, _ts(o.updated_at)):
        headers = {"Last-Modified": last_mod}
        if etag:
            headers["ETag"] = etag
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    base_headers = {
        "Accept-Ranges": "bytes",
        "Last-Modified": last_mod,
    }
    if etag:
        base_headers["ETag"] = etag

    if head:
        return Response(
            status_code=200,
            headers={"Content-Length": str(o.size or 0), "Content-Type": mime, **base_headers},
        )

    try:
        url = qiniu_client.private_url(o.key)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    # 会跟随 302 的客户端：直连七牛，省后端流量
    if _follows_redirect(request):
        return RedirectResponse(url, status_code=status.HTTP_302_FOUND)

    # 其余：后端代理字节，透传 Range（分段读预览/流媒体/续传）
    up_headers = {"Accept-Encoding": "identity"}
    rng = request.headers.get("Range")
    if rng:
        up_headers["Range"] = rng
    r = _plain_http.get(url, stream=True, timeout=30, headers=up_headers)
    if r.status_code not in (200, 206):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"七牛取回失败：{r.status_code}")
    resp_headers = dict(base_headers)
    for h in ("Content-Length", "Content-Range"):
        if r.headers.get(h):
            resp_headers[h] = r.headers[h]
    return StreamingResponse(
        r.iter_content(64 * 1024),
        status_code=r.status_code,
        media_type=mime,
        headers=resp_headers,
    )


# ---------------- 写方法（PUT / DELETE / MKCOL / MOVE / COPY） ----------------


def _dest_rel(request: Request) -> str:
    """解析 MOVE/COPY 的 Destination 头（形如 https://host<prefix>/新/路径）为相对路径。

    前缀随 vhost 变（主域 `/dav`、子域名根 ``），按本次请求的 `X-Dav-Prefix` 剥离。
    """
    dest = request.headers.get("Destination")
    if not dest:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少 Destination")
    path = urlparse(dest).path
    prefix = _dav_prefix(request)
    if prefix and path.startswith(prefix):
        path = path[len(prefix):]
    return unquote(path).strip("/")


def _require_parent(db: Session, rel: str) -> None:
    """WebDAV 语义：写入路径的父目录必须已存在，否则 409（客户端应先 MKCOL）。"""
    parent = _parent_rel(rel)
    if parent and _get(db, parent) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "父目录不存在")


def _purge_subtree(db: Session, rel: str) -> None:
    """删除 rel 及其后代（含七牛对象）；调用方负责 commit/flush。"""
    for node in _subtree(db, rel):
        if not node.is_dir and node.key:
            try:
                qiniu_client.delete(node.key)
            except RuntimeError:
                pass  # 清理失败不阻断删除本身
        db.delete(node)


async def _spool_body(request: Request) -> tuple[str, int]:
    """把请求体**流式**落到临时文件，返回 (临时文件路径, 字节数)。

    边收边写、内存恒定（不 `await request.body()` 把整包读进内存），故拖再大的
    文件也不会顶爆内存；随后交给 qiniu_client.proxy_upload_file 分片上传。
    """
    size = 0
    fd, path = tempfile.mkstemp(prefix="dav-put-")
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                if chunk:
                    f.write(chunk)
                    size += len(chunk)
    except BaseException:
        os.unlink(path)
        raise
    return path, size


async def _put(request: Request, db: Session, rel: str) -> Response:
    if not rel:
        raise HTTPException(status.HTTP_405_METHOD_NOT_ALLOWED, "不能写入根")
    existing = _get(db, rel)
    if existing and existing.is_dir:
        raise HTTPException(status.HTTP_405_METHOD_NOT_ALLOWED, "目标是目录")
    _require_parent(db, rel)

    mime = _guess_mime(_basename(rel))
    # 覆盖也写新 key、再删旧 key：七牛 CDN 按 URL 缓存，同 key 覆盖读回旧缓存
    ext = ("." + _ext(rel)) if _ext(rel) else ""
    key = f"{uuid.uuid4().hex}{ext}"

    tmp_path, size = await _spool_body(request)
    try:
        qiniu_client.proxy_upload_file(key, tmp_path, mime)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    old_key = existing.key if existing else None
    if existing:
        existing.key, existing.size, existing.content_type = key, size, mime
    else:
        db.add(FileObject(
            path=rel, is_dir=False, key=key, content_type=mime, size=size
        ))
    db.commit()
    if old_key and old_key != key:
        try:
            qiniu_client.delete(old_key)
        except RuntimeError:
            pass
    return Response(status_code=204 if existing else 201)


def _delete(db: Session, rel: str) -> Response:
    if not rel:
        raise HTTPException(status.HTTP_405_METHOD_NOT_ALLOWED, "不能删除根")
    if _get(db, rel) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "资源不存在")
    _purge_subtree(db, rel)
    db.commit()
    return Response(status_code=204)


def _mkcol(db: Session, rel: str) -> Response:
    if not rel:
        raise HTTPException(status.HTTP_405_METHOD_NOT_ALLOWED, "根已存在")
    if _get(db, rel) is not None:
        raise HTTPException(status.HTTP_405_METHOD_NOT_ALLOWED, "已存在同名项")
    _require_parent(db, rel)
    db.add(FileObject(path=rel, is_dir=True, key=None, size=0))
    db.commit()
    return Response(status_code=201)


def _move_or_copy(request: Request, db: Session, rel: str, is_move: bool) -> Response:
    if not rel:
        raise HTTPException(status.HTTP_405_METHOD_NOT_ALLOWED, "不能操作根")
    if _get(db, rel) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "源不存在")
    dst_rel = _dest_rel(request)
    if not dst_rel:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法目标")
    if dst_rel == rel or dst_rel.startswith(rel + "/"):
        raise HTTPException(status.HTTP_409_CONFLICT, "不能移动/复制到自身或子目录")

    existing = _get(db, dst_rel)
    if existing is not None:
        overwrite = request.headers.get("Overwrite", "T").upper() != "F"
        if not overwrite:
            raise HTTPException(status.HTTP_412_PRECONDITION_FAILED, "目标已存在")
        _purge_subtree(db, dst_rel)
        db.flush()
    _require_parent(db, dst_rel)

    if is_move:
        _reparent(db, rel, dst_rel)
    else:
        for o in _subtree(db, rel):
            dst_path = dst_rel + o.path[len(rel):]
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
    return Response(status_code=204 if existing is not None else 201)


def _lock(request: Request, rel: str) -> Response:
    """假锁：总是授予并回 opaquelocktoken（单管理员无写并发，仅让 Finder 认为可写）。"""
    prefix = _dav_prefix(request)
    token = f"opaquelocktoken:{uuid.uuid4()}"
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<D:prop xmlns:D="DAV:"><D:lockdiscovery><D:activelock>'
        "<D:locktype><D:write/></D:locktype>"
        "<D:lockscope><D:exclusive/></D:lockscope>"
        "<D:depth>infinity</D:depth>"
        "<D:timeout>Second-3600</D:timeout>"
        f"<D:locktoken><D:href>{token}</D:href></D:locktoken>"
        f"<D:lockroot><D:href>{escape(_href(prefix, rel, False))}</D:href></D:lockroot>"
        "</D:activelock></D:lockdiscovery></D:prop>"
    )
    return Response(
        content=body,
        status_code=200,
        media_type='application/xml; charset="utf-8"',
        headers={"Lock-Token": f"<{token}>"},
    )


# ---------------- 路由 ----------------

_READ_METHODS = ["OPTIONS", "PROPFIND", "HEAD", "GET"]
_WRITE_METHODS = ["PUT", "DELETE", "MKCOL", "MOVE", "COPY", "LOCK", "UNLOCK"]

_ALLOW = ", ".join(_READ_METHODS + _WRITE_METHODS)


@router.api_route("", methods=_READ_METHODS, include_in_schema=False)
@router.api_route("/{dav_path:path}", methods=_READ_METHODS, include_in_schema=False)
def dav_entry(
    request: Request,
    dav_path: str = "",
    db: Session = Depends(get_db),
) -> Response:
    if request.method == "OPTIONS":
        return Response(
            status_code=200,
            headers={
                "DAV": "1, 2",
                "Allow": _ALLOW,
                "MS-Author-Via": "DAV",
                "Content-Length": "0",
            },
        )

    _dav_user(request, db)
    rel = dav_path.strip("/")
    if request.method == "PROPFIND":
        return _propfind(request, db, rel)
    return _get_or_head(request, db, rel, head=(request.method == "HEAD"))


@router.api_route("", methods=_WRITE_METHODS, include_in_schema=False)
@router.api_route("/{dav_path:path}", methods=_WRITE_METHODS, include_in_schema=False)
async def dav_write(
    request: Request,
    dav_path: str = "",
    db: Session = Depends(get_db),
) -> Response:
    _dav_user(request, db)
    rel = dav_path.strip("/")
    method = request.method
    if method == "PUT":
        return await _put(request, db, rel)
    if method == "DELETE":
        return _delete(db, rel)
    if method == "MKCOL":
        return _mkcol(db, rel)
    if method == "MOVE":
        return _move_or_copy(request, db, rel, is_move=True)
    if method == "COPY":
        return _move_or_copy(request, db, rel, is_move=False)
    if method == "LOCK":
        return _lock(request, rel)
    # UNLOCK
    return Response(status_code=204)
