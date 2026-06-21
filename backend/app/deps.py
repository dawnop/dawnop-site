"""FastAPI 依赖项：数据库会话等。当前用户依赖在阶段 2 鉴权中补充。"""
from collections.abc import Generator

from sqlalchemy.orm import Session

from app.database import SessionLocal


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
