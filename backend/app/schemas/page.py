"""页面相关的请求/响应模型。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.article import _clean_slug, _clean_title

PageType = Literal["content", "article_list"]


class PageCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    type: PageType = "content"
    description: str = ""
    content: str = ""
    auto_title: bool = False
    nav_visible: bool = True
    nav_order: int = 0
    slug: str | None = Field(default=None, max_length=255)
    created_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v):
        return _clean_title(v)

    @field_validator("slug")
    @classmethod
    def _strip_slug(cls, v):
        return _clean_slug(v)


class PageUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=255)
    type: PageType | None = None
    description: str | None = None
    content: str | None = None
    auto_title: bool | None = None
    nav_visible: bool | None = None
    nav_order: int | None = None
    created_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v):
        return _clean_title(v)

    @field_validator("slug")
    @classmethod
    def _strip_slug(cls, v):
        return _clean_slug(v)


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    type: str
    description: str
    content: str
    auto_title: bool
    nav_visible: bool
    nav_order: int
    created_at: datetime
    updated_at: datetime


class NavItem(BaseModel):
    """导航栏条目（公开）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    type: str
    nav_order: int


class ReorderRequest(BaseModel):
    # 按期望顺序排列的 page id 列表
    ids: list[int]
