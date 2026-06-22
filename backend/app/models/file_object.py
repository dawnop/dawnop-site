"""FileObject 模型：文件与文件夹的元信息（文件本体在七牛）。

文件夹也用一行表示（is_dir=True，key 为空）；这样空文件夹可持久化，
列表/重命名/移动都只是改 path（七牛 key 保持不变，是不透明 uuid）。
`path` 为不含 storage 前缀的相对路径，如 `docs/img/cover.png` 或文件夹 `docs/img`。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FileObject(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 相对路径（唯一），文件与文件夹共用
    path: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    is_dir: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # 七牛对象 key（不透明 uuid）；文件夹为空
    key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_type: Mapped[str] = mapped_column(String(128), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
