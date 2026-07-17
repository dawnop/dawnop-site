"""PendingUpload 模型：前端直传的「已签发凭证、待登记」账本。

直传流程是 /upload-token 签 key → 前端直传七牛 → /register 登记。若前端在
直传成功后、登记前中断（关页/断网/进程被杀），该 key 就成了七牛上的孤儿对象。

每签发一个上传凭证就在此记一行；/register 成功后删除对应行。于是「孤儿」= 本表里
**本环境亲自签发过、却始终没登记成功**的 key——与七牛空间里其它对象（可能是共用
空间的另一环境登记的）无关，因此清理时不会误伤，共用空间也安全。
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PendingUpload(Base):
    __tablename__ = "pending_uploads"

    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    path: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
