"""Page 模型：后台可管理的导航页面。

type:
- "content"      内容页，正文为 Markdown（如「关于」）。
- "article_list" 文章列表页（同时充当「分类」），展示分配到本页的文章。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(32), default="content")  # content | article_list
    description: Mapped[str] = mapped_column(Text, default="")  # 页面摘要 / SEO 描述
    content: Mapped[str] = mapped_column(Text, default="")  # 仅内容页使用
    nav_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    nav_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
