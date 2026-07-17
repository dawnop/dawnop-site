"""搜索相关的响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.tag import TagOut


class SearchItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    created_at: datetime
    word_count: int
    tags: list[TagOut] = []
    # 关键词高亮后的安全 HTML（仅含 <mark>）；前端可直接 v-html
    title_html: str
    excerpt_html: str


class SearchResponse(BaseModel):
    total: int
    page: int
    size: int
    query: str
    items: list[SearchItem]
