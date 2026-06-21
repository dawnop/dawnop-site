"""SQLAlchemy engine / session / Base 定义。"""
from sqlalchemy import create_engine, inspect, text
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


def _sqlite_add_missing_columns() -> None:
    """对已存在的旧库补加新列（无 Alembic 时的轻量迁移）。

    create_all 不会修改既有表，这里只为 SQLite 补加可空列。
    """
    if not settings.database_url.startswith("sqlite"):
        return
    insp = inspect(engine)
    if "articles" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("articles")}
    if "page_id" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE articles ADD COLUMN page_id INTEGER"))


def init_db() -> None:
    """创建所有表。导入 models 以注册到 Base.metadata。"""
    from app import models  # noqa: F401  确保模型被加载

    Base.metadata.create_all(bind=engine)
    _sqlite_add_missing_columns()
