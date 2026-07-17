"""动态可视化组件的请求/响应模型。

slug 是文章 ```viz <slug>``` 围栏里写的标识，必须显式提供且为 [a-z0-9-]
（与前端 markdown-it 围栏的白名单一致），不走「按标题自动生成」。
"""

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _clean_slug(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip().lower()
    if not _SLUG_RE.match(v):
        raise ValueError("标识只能用小写字母、数字和连字符（如 my-viz）")
    return v


def _clean_source(v: str | None) -> str | None:
    if v is None:
        return None
    if not v.strip():
        raise ValueError("组件源码不能为空")
    return v


class VizCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=255)
    name: str = ""
    source: str = Field(min_length=1)
    compiled: str = ""
    style: str = ""

    @field_validator("slug")
    @classmethod
    def _v_slug(cls, v):
        return _clean_slug(v)

    @field_validator("source")
    @classmethod
    def _v_source(cls, v):
        return _clean_source(v)


class VizUpdate(BaseModel):
    slug: str | None = Field(default=None, max_length=255)
    name: str | None = None
    source: str | None = None
    compiled: str | None = None
    style: str | None = None

    @field_validator("slug")
    @classmethod
    def _v_slug(cls, v):
        return _clean_slug(v)

    @field_validator("source")
    @classmethod
    def _v_source(cls, v):
        return _clean_source(v)


class VizOut(BaseModel):
    """完整记录（管理用，含源码与产物）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    source: str
    compiled: str
    style: str
    created_at: datetime
    updated_at: datetime


class VizPublic(BaseModel):
    """公开读取：只给读者挂载所需字段，不含源码。"""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    compiled: str
    style: str
