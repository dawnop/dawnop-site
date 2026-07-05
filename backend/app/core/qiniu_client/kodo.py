"""七牛 Kodo 对象操作：上传凭证、代理上传、私有下载 URL、stat、删除、复制。

空间为**私有**：下载/预览均通过带签名、有时效的 URL（private_download_url）。
所有函数失败时抛 RuntimeError，由上层转换为 HTTP 错误。
"""
from functools import lru_cache
from urllib.parse import quote

import requests
from qiniu import Auth, BucketManager, put_data, put_file

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
    """服务端代理上传字节流到七牛，返回七牛响应 ret。

    整块 bytes 一次性上传，适合小内容（create-file 空文件、/save 文本编辑等）。
    大文件走 proxy_upload_file（磁盘流式、自动分片续传），避免内存峰值。
    """
    token = _auth().upload_token(settings.qiniu_bucket, key, 3600)
    ret, info = put_data(
        token, key, data, mime_type=mime or "application/octet-stream"
    )
    if ret is None or info.status_code != 200:
        raise RuntimeError(f"七牛上传失败: {info}")
    return ret


def proxy_upload_file(key: str, filepath: str, mime: str | None = None) -> dict:
    """从**本地文件**上传到七牛（内存恒定）。

    put_file 按大小自动选择：小文件表单直传、大文件分片续传（从磁盘按块读、
    可 seek 重试），故上传任意大小都不会把整个文件读进内存。WebDAV PUT 用它：
    先把请求体流式落到临时文件，再交此函数上传。
    """
    token = _auth().upload_token(settings.qiniu_bucket, key, 3600)
    ret, info = put_file(
        token, key, filepath, mime_type=mime or "application/octet-stream"
    )
    if ret is None or info.status_code != 200:
        raise RuntimeError(f"七牛上传失败: {info}")
    return ret


def private_url(
    key: str,
    expires: int | None = None,
    attname: str | None = None,
    fop: str | None = None,
) -> str:
    """生成私有空间的带签名下载/预览 URL。

    attname 不为空时通过七牛 `?attname=` 强制以附件（指定文件名）下载；
    为空则浏览器内联预览。
    fop 不为空时（如 `imageView2/2/w/320/format/webp`）追加图片处理指令，
    生成缩略图——**必须先拼 fop 再签名**（fop 是被签名 URL 的一部分）。
    """
    domain = settings.qiniu_domain.rstrip("/")
    base_url = f"{domain}/{key}"
    query = []
    if fop:
        query.append(fop)
    if attname:
        query.append(f"attname={quote(attname)}")
    if query:
        base_url += "?" + "&".join(query)
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
