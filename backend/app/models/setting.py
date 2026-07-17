"""Setting 模型：后台全局配置项（key-value，value 存 JSON 编码字符串）。

默认值定义在 api/settings.py 的 DEFAULTS；库里只存被改过的项，
读取时与默认值合并，因此新增配置项不需要数据迁移。
"""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(4096), default="")
