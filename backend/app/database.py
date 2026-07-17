"""SQLAlchemy engine / session / Base 定义。"""

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# SQLite 在多线程（uvicorn）下需要关闭同线程检查
connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _simple_extension_path() -> str:
    """simple 分词扩展路径：优先 .env 配置，否则用 backend/extensions/libsimple。

    路径可不带后缀，sqlite 会按平台自动补 .so/.dylib/.dll。
    """
    configured = settings.simple_extension_path.strip()
    if configured:
        return configured
    return str(Path(__file__).resolve().parent.parent / "extensions" / "libsimple")


def _load_sqlite_extensions(dbapi_conn, _record) -> None:
    """每条新连接加载 simple 分词扩展。缺文件/不支持则静默跳过（搜索退回 trigram/LIKE）。"""
    if not hasattr(dbapi_conn, "enable_load_extension"):
        return
    try:
        dbapi_conn.enable_load_extension(True)
        dbapi_conn.load_extension(_simple_extension_path())
    except Exception:
        pass
    finally:
        try:
            dbapi_conn.enable_load_extension(False)
        except Exception:
            pass


if settings.database_url.startswith("sqlite"):
    event.listen(engine, "connect", _load_sqlite_extensions)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """创建所有表。导入 models 以注册到 Base.metadata。"""
    from app import models  # noqa: F401  确保模型被加载

    Base.metadata.create_all(bind=engine)
