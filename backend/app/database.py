"""SQLAlchemy engine / session / Base 定义。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# SQLite 在多线程（uvicorn）下需要关闭同线程检查
connect_args = (
    {"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {}
)

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """创建所有表。导入 models 以注册到 Base.metadata。"""
    from app import models  # noqa: F401  确保模型被加载

    Base.metadata.create_all(bind=engine)
