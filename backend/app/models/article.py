"""Article 模型：博客文章，正文为 Markdown 文本。"""

import re
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.tag import Tag

# 中文按字符、英文/数字按词计数，作为字数与阅读时间的粗略估算（含少量 Markdown 标记，可接受）
_CJK = re.compile(r"[一-鿿]")
_WORD = re.compile(r"[A-Za-z0-9]+")


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # 是否用正文首个 H1 作标题（前端开关）：为真时文章页渲染会隐藏正文那行 H1，避免与标题重复
    auto_title: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    # 浏览量：匿名访客成功读取「已发布」文章时 +1（后台展示，前端暂不显示）
    views: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    # 一对多：文章归属一个文章列表页（=分类）；删除页面时置空
    page_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("pages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 多对多：标签。selectin 预加载避免 N+1；按名排序稳定输出
    tags: Mapped[list["Tag"]] = relationship(
        "Tag", secondary="article_tags", lazy="selectin", order_by="Tag.name"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def word_count(self) -> int:
        """正文字数估算：中文字符数 + 英文/数字词数。Pydantic from_attributes 会读取此属性。"""
        text = self.content or ""
        return len(_CJK.findall(text)) + len(_WORD.findall(text))
