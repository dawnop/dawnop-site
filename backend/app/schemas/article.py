"""文章相关的请求/响应模型。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import PageResponse
from app.schemas.tag import TagOut


def _clean_title(v: str | None) -> str | None:
    """去首尾空白；用于创建时非空校验与更新时的规整。"""
    if v is None:
        return None
    v = v.strip()
    if not v:
        raise ValueError("标题不能为空")
    return v


def _clean_slug(v: str | None) -> str | None:
    """空白 slug 视为未提供（交由后端按标题生成）。"""
    if v is None:
        return None
    v = v.strip()
    return v or None


class ArticleBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    summary: str = ""
    content: str = ""
    published: bool = False
    auto_title: bool = False
    page_id: int | None = None
    tags: list[str] = []

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v):
        return _clean_title(v)


class ArticleCreate(ArticleBase):
    # 不传则由后端按 title 生成
    slug: str | None = Field(default=None, max_length=255)
    # 发布时间（前台显示/排序用）；不传则用当前时间
    created_at: datetime | None = None

    @field_validator("slug")
    @classmethod
    def _strip_slug(cls, v):
        return _clean_slug(v)


class ArticleUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=255)
    summary: str | None = None
    content: str | None = None
    published: bool | None = None
    auto_title: bool | None = None
    page_id: int | None = None
    tags: list[str] | None = None
    created_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v):
        return _clean_title(v)

    @field_validator("slug")
    @classmethod
    def _strip_slug(cls, v):
        return _clean_slug(v)


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    summary: str
    content: str
    published: bool
    auto_title: bool
    page_id: int | None
    word_count: int
    views: int
    tags: list[TagOut] = []
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
    views: int
    tags: list[TagOut] = []
    created_at: datetime
    updated_at: datetime


class ArticleListResponse(PageResponse[ArticleListItem]):
    """文章分页响应（沿用统一的 PageResponse 形状）。"""
