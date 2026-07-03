"""七牛云 Kodo 封装：上传凭证、代理上传、私有下载 URL、stat、删除。

空间为**私有**：下载/预览均通过带签名、有时效的 URL（private_download_url）。
所有函数失败时抛 RuntimeError，由上层转换为 HTTP 错误。
"""
from functools import lru_cache
from urllib.parse import quote

import requests
from qiniu import Auth, BucketManager, QiniuMacAuth, put_data

from app.config import settings


@lru_cache
def _auth() -> Auth:
    if not settings.qiniu_access_key or not settings.qiniu_secret_key:
        raise RuntimeError("未配置 QINIU_ACCESS_KEY / QINIU_SECRET_KEY")
    return Auth(settings.qiniu_access_key, settings.qiniu_secret_key)


@lru_cache
def _bucket_manager() -> BucketManager:
    return BucketManager(_auth())


def _expires(expires: int | None) -> int:
    return expires or settings.qiniu_token_expires


def upload_token(key: str, expires: int | None = None) -> str:
    """签发**限定到具体 key** 的上传凭证，供前端直传（不下发密钥）。"""
    return _auth().upload_token(settings.qiniu_bucket, key, _expires(expires))


@lru_cache
def upload_host() -> str:
    """解析当前空间所在区域的上传域名，供前端直传 POST。

    通过七牛 v4/query 接口（只需公开的 AccessKey + bucket）拿到区域上传域名；
    失败时回退到默认上传域名。结果缓存（区域不会变）。
    """
    try:
        resp = requests.get(
            "https://api.qiniu.com/v4/query",
            params={"ak": settings.qiniu_access_key, "bucket": settings.qiniu_bucket},
            timeout=10,
        )
        domains = resp.json()["hosts"][0]["up"]["domains"]
        if domains:
            return f"https://{domains[0]}"
    except Exception:  # noqa: BLE001  网络/结构异常都回退默认域名
        pass
    return "https://upload.qiniup.com"


def proxy_upload(key: str, data: bytes, mime: str | None = None) -> dict:
    """服务端代理上传字节流到七牛，返回七牛响应 ret。"""
    token = _auth().upload_token(settings.qiniu_bucket, key, 3600)
    ret, info = put_data(
        token, key, data, mime_type=mime or "application/octet-stream"
    )
    if ret is None or info.status_code != 200:
        raise RuntimeError(f"七牛上传失败: {info}")
    return ret


def private_url(
    key: str, expires: int | None = None, attname: str | None = None
) -> str:
    """生成私有空间的带签名下载/预览 URL。

    attname 不为空时通过七牛 `?attname=` 强制以附件（指定文件名）下载；
    为空则浏览器内联预览。
    """
    domain = settings.qiniu_domain.rstrip("/")
    base_url = f"{domain}/{key}"
    if attname:
        base_url += f"?attname={quote(attname)}"
    return _auth().private_download_url(base_url, expires=_expires(expires))


def stat(key: str) -> dict:
    """查询文件元信息（fsize、mimeType、hash 等）。"""
    ret, info = _bucket_manager().stat(settings.qiniu_bucket, key)
    if ret is None or info.status_code != 200:
        raise RuntimeError(f"七牛 stat 失败: {info}")
    return ret


def delete(key: str) -> None:
    """删除七牛上的对象。key 不存在（612）也视为已删除。"""
    ret, info = _bucket_manager().delete(settings.qiniu_bucket, key)
    if info.status_code not in (200, 612):
        raise RuntimeError(f"七牛删除失败: {info}")


def copy(src_key: str, dst_key: str) -> None:
    """在同一空间内复制对象（用于复制文件/文件夹）。"""
    bucket = settings.qiniu_bucket
    ret, info = _bucket_manager().copy(bucket, src_key, bucket, dst_key, force="true")
    if info.status_code != 200:
        raise RuntimeError(f"七牛复制失败: {info}")


# 统计 API 调用忽略代理环境变量（开发机本机代理对七牛时好时坏，服务端直连即可）
_plain_http = requests.Session()
_plain_http.trust_env = False


def bucket_space() -> int:
    """空间当前存储量（字节），来自七牛统计 API（GET api.qiniuapi.com/v6/space）。

    鉴权用 Qiniu 签名（QiniuMacAuth，非 QBox）。统计延迟约 5 分钟、按天出点，
    取最近 7 天里最后一个非空点。注意它统计的是**空间实际对象**，会包含
    register 失败等产生的孤儿对象，可能大于本地元数据求和。
    未配置密钥或调用失败抛 RuntimeError，由上层决定兜底。
    """
    from datetime import datetime, timedelta

    if not settings.qiniu_access_key or not settings.qiniu_secret_key:
        raise RuntimeError("未配置 QINIU_ACCESS_KEY / QINIU_SECRET_KEY")
    now = datetime.now()
    begin = (now - timedelta(days=7)).strftime("%Y%m%d000000")
    end = now.strftime("%Y%m%d%H%M%S")
    host = "api.qiniuapi.com"
    url = f"http://{host}/v6/space?bucket={settings.qiniu_bucket}&begin={begin}&end={end}&g=day"
    mac = QiniuMacAuth(settings.qiniu_access_key, settings.qiniu_secret_key)
    token = mac.token_of_request(
        method="GET",
        host=host,
        url=url,
        qheaders="",
        content_type="application/x-www-form-urlencoded",
    )
    r = _plain_http.get(
        url,
        headers={
            "Authorization": f"Qiniu {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=10,
    )
    if r.status_code != 200:
        raise RuntimeError(f"七牛空间统计失败: {r.status_code}")
    datas = [v for v in (r.json().get("datas") or []) if v]
    if not datas:
        raise RuntimeError("七牛空间统计无数据")
    return int(datas[-1])
