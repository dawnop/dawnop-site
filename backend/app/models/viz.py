"""VizComponent 模型：后台可编辑的动态交互可视化组件。

存 Vue SFC 源码（source）与「后台浏览器保存时编译出的产物」（compiled + style）。
读者端只取 compiled/style 直接 eval 挂载，不加载编译器（见前端 viz/runtime.js）。
slug 即文章里 ```viz <slug>``` 围栏写的标识。
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class VizComponent(Base):
    __tablename__ = "viz_components"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    source: Mapped[str] = mapped_column(Text, default="")  # SFC 源码
    compiled: Mapped[str] = mapped_column(Text, default="")  # 编译产物（JS 模块工厂体）
    style: Mapped[str] = mapped_column(Text, default="")  # 抽出的 scoped CSS
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
