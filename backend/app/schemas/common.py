"""通用 schema：统一分页响应。"""

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PageResponse(BaseModel, Generic[T]):
    total: int
    page: int
    size: int
    items: list[T]
