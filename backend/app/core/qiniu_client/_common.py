"""七牛统计/CDN 接口共用：直连 HTTP 会话 + 密钥校验。"""
import requests

from app.config import settings

# 统计/CDN API 调用忽略代理环境变量（开发机本机代理对七牛时好时坏，服务端直连即可）
_plain_http = requests.Session()
_plain_http.trust_env = False


def _require_keys() -> None:
    if not settings.qiniu_access_key or not settings.qiniu_secret_key:
        raise RuntimeError("未配置 QINIU_ACCESS_KEY / QINIU_SECRET_KEY")
