"""文章相关的请求/响应模型。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ArticleBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    summary: str = ""
    content: str = ""
    published: bool = False
    page_id: int | None = None


class ArticleCreate(ArticleBase):
    # 不传则由后端按 title 生成
    slug: str | None = Field(default=None, max_length=255)
    # 发布时间（前台显示/排序用）；不传则用当前时间
    created_at: datetime | None = None


class ArticleUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=255)
    summary: str | None = None
    content: str | None = None
    published: bool | None = None
    page_id: int | None = None
    created_at: datetime | None = None


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    summary: str
    content: str
    published: bool
    page_id: int | None
    word_count: int
    created_at: datetime
    updated_at: datetime


class ArticleListItem(BaseModel):
    """列表项：不含正文，减小传输体积（保留 word_count 供列表显示阅读时间）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    summary: str
    published: bool
    page_id: int | None
    word_count: int
    created_at: datetime
    updated_at: datetime


class ArticleListResponse(BaseModel):
    total: int
    page: int
    size: int
    items: list[ArticleListItem]
