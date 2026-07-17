"""Tag 模型：文章标签，文章↔标签多对多。"""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Table, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# 文章 ↔ 标签 多对多关联表；任一侧删除时级联清理关联行（不删对方实体）
article_tags = Table(
    "article_tags",
    Base.metadata,
    Column(
        "article_id", ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
