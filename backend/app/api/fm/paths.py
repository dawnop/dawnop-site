"""文件管理器路径工具：storage 前缀、父目录、扩展名、mime、时间戳等纯函数。"""
import mimetypes
from datetime import datetime

STORAGE = "qiniu"


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
