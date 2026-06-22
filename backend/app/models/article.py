"""Article 模型：博客文章，正文为 Markdown 文本。"""
import re
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

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
    # 一对多：文章归属一个文章列表页（=分类）；删除页面时置空
    page_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("pages.id", ondelete="SET NULL"), nullable=True, index=True
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
