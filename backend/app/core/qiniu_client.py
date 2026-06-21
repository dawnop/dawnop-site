"""七牛云 Kodo 封装：上传凭证、代理上传、私有下载 URL、stat、删除。

空间为**私有**：下载/预览均通过带签名、有时效的 URL（private_download_url）。
所有函数失败时抛 RuntimeError，由上层转换为 HTTP 错误。
"""
from functools import lru_cache

from qiniu import Auth, BucketManager, put_data

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
    """签发限定 key 的上传凭证，供前端直传。"""
    return _auth().upload_token(settings.qiniu_bucket, key, _expires(expires))


def proxy_upload(key: str, data: bytes, mime: str | None = None) -> dict:
    """服务端代理上传字节流到七牛，返回七牛响应 ret。"""
    token = _auth().upload_token(settings.qiniu_bucket, key, 3600)
    ret, info = put_data(
        token, key, data, mime_type=mime or "application/octet-stream"
    )
    if ret is None or info.status_code != 200:
        raise RuntimeError(f"七牛上传失败: {info}")
    return ret


def private_url(key: str, expires: int | None = None) -> str:
    """生成私有空间的带签名下载/预览 URL。"""
    domain = settings.qiniu_domain.rstrip("/")
    base_url = f"{domain}/{key}"
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
