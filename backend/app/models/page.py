"""Page 模型：后台可管理的导航页面。

type:
- "content"      内容页，正文为 Markdown（如「关于」）。
- "article_list" 文章列表页（同时充当「分类」），展示分配到本页的文章。
- "builtin"      内置页（首页、标签页等 SPA 固定路由）：启动时幂等注入，
                 页面管理里可改导航名称/显隐/排序，不可删除、不可改 slug/内容。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# 内置页注册表：slug → 前端固定路由。新增系统页（如搜索）时在此登记并补 BUILTIN_PAGES。
BUILTIN_PATHS = {"home": "/", "tags": "/tags"}

# 启动注入的内置页初始值；nav_order 取极值让首页默认最前、标签页默认最后
BUILTIN_PAGES = [
    {"slug": "home", "title": "首页", "nav_order": -1000},
    {"slug": "tags", "title": "标签", "nav_order": 1000},
]


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(32), default="content")  # content | article_list
    description: Mapped[str] = mapped_column(Text, default="")  # 页面摘要 / SEO 描述
    content: Mapped[str] = mapped_column(Text, default="")  # 仅内容页使用
    # 是否用正文首个 H1 作标题（前端开关，仅内容页有意义）：为真时页面渲染隐藏正文那行 H1
    auto_title: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    nav_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    nav_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def path(self) -> str:
        """前台访问路径：内置页走固定路由，其余走 /p/{slug}。"""
        if self.type == "builtin":
            return BUILTIN_PATHS.get(self.slug, "/")
        return f"/p/{self.slug}"
