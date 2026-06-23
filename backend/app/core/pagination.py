"""统一分页：把已排序的 query 切片成 {total, page, size, items} 字典。

调用方负责先 .filter()/.order_by()，本函数只做计数与切片，配合
schemas.common.PageResponse 形成一致的分页响应。
"""
from sqlalchemy.orm import Query


def paginate(query: Query, page: int, size: int) -> dict:
    total = query.count()
    items = query.offset((page - 1) * size).limit(size).all()
    return {"total": total, "page": page, "size": size, "items": items}
