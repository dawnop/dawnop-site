"""轻量 CRUD 辅助：消除各路由里重复的「取不到就 404」与无意义更新过滤。"""

from typing import TypeVar

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.database import Base

ModelT = TypeVar("ModelT", bound=Base)


def get_or_404(db: Session, model: type[ModelT], obj_id: int, detail: str) -> ModelT:
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail)
    return obj


def drop_null_created_at(data: dict) -> dict:
    """更新时若显式传了 created_at=None，移除它，避免把发布时间清空。"""
    if data.get("created_at") is None:
        data.pop("created_at", None)
    return data
