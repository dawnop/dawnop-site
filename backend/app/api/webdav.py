"""只读 WebDAV 适配层：把 FileObject 的 path↔key 树以标准 WebDAV 暴露，
供 Finder / RaiDrive / rclone / Mountain Duck 等挂载浏览、预览、下载。

只实现读方法（OPTIONS / PROPFIND / HEAD / GET），只广播 DAV class 1（无 LOCK），
客户端据此把挂载识别为**只读**；写方法（PUT/DELETE/MKCOL/MOVE/COPY/LOCK…）
未在路由里注册，Starlette 自动回 405。写入留待后续阶段。

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
import time
from email.utils import formatdate
from urllib.parse import quote
from xml.sax.saxutils import escape

import requests
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.fm import _children, _get, _ts
from app.core import qiniu_client
from app.core.security import verify_password
from app.deps import get_db
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


# ---------------- PROPFIND XML ----------------


def _href(rel: str, is_dir: bool) -> str:
    segs = rel.split("/") if rel else []
    path = "/".join(quote(s) for s in segs)
    href = f"{DAV_PREFIX}/{path}" if path else f"{DAV_PREFIX}/"
    if is_dir and not href.endswith("/"):
        href += "/"
    return href


def _http_date(ts: int | None) -> str:
    return formatdate(ts if ts is not None else time.time(), usegmt=True)


def _iso_date(ts: int | None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts if ts else time.time()))


def _response_xml(
    rel: str,
    name: str,
    is_dir: bool,
    size: int,
    mime: str,
    mtime: int | None,
    ctime: int | None,
) -> str:
    if is_dir:
        typ = "<D:collection/>"
        extra = ""
    else:
        typ = ""
        extra = (
            f"<D:getcontentlength>{size}</D:getcontentlength>"
            f"<D:getcontenttype>{escape(mime or 'application/octet-stream')}</D:getcontenttype>"
        )
    return (
        "<D:response>"
        f"<D:href>{escape(_href(rel, is_dir))}</D:href>"
        "<D:propstat><D:prop>"
        f"<D:displayname>{escape(name)}</D:displayname>"
        f"<D:resourcetype>{typ}</D:resourcetype>"
        f"<D:getlastmodified>{_http_date(mtime)}</D:getlastmodified>"
        f"<D:creationdate>{_iso_date(ctime)}</D:creationdate>"
        f"{extra}"
        "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>"
        "</D:response>"
    )


def _entry_xml_from_obj(o) -> str:
    name = o.path.rsplit("/", 1)[-1] if o.path else ""
    return _response_xml(
        o.path, name, o.is_dir, o.size or 0, o.content_type or "",
        _ts(o.updated_at), _ts(o.created_at),
    )


def _propfind(request: Request, db: Session, rel: str) -> Response:
    depth = request.headers.get("Depth", "1")
    parts = []

    if rel == "":
        parts.append(_response_xml("", "", True, 0, "", None, None))
        target_is_dir = True
    else:
        o = _get(db, rel)
        if o is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "资源不存在")
        parts.append(_entry_xml_from_obj(o))
        target_is_dir = o.is_dir

    if depth != "0" and target_is_dir:
        for kid in _children(db, rel):
            parts.append(_entry_xml_from_obj(kid))

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
    if head:
        return Response(
            status_code=200,
            headers={
                "Content-Length": str(o.size or 0),
                "Content-Type": mime,
                "Accept-Ranges": "bytes",
                "Last-Modified": _http_date(_ts(o.updated_at)),
            },
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
    resp_headers = {"Accept-Ranges": "bytes"}
    for h in ("Content-Length", "Content-Range"):
        if r.headers.get(h):
            resp_headers[h] = r.headers[h]
    return StreamingResponse(
        r.iter_content(64 * 1024),
        status_code=r.status_code,
        media_type=mime,
        headers=resp_headers,
    )


# ---------------- 路由（读方法；写方法未注册 → 自动 405） ----------------

_READ_METHODS = ["OPTIONS", "PROPFIND", "HEAD", "GET"]


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
                "DAV": "1",
                "Allow": "OPTIONS, PROPFIND, HEAD, GET",
                "MS-Author-Via": "DAV",
                "Content-Length": "0",
            },
        )

    _dav_user(request, db)
    rel = dav_path.strip("/")
    if request.method == "PROPFIND":
        return _propfind(request, db, rel)
    return _get_or_head(request, db, rel, head=(request.method == "HEAD"))
