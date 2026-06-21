"""页面相关的请求/响应模型。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PageType = Literal["content", "article_list"]


class PageCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    type: PageType = "content"
    content: str = ""
    nav_visible: bool = True
    nav_order: int = 0
    slug: str | None = Field(default=None, max_length=255)


class PageUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=255)
    type: PageType | None = None
    content: str | None = None
    nav_visible: bool | None = None
    nav_order: int | None = None


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    type: str
    content: str
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
