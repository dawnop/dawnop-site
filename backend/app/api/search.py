"""全站搜索接口（公开，仅已发布文章）。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.search import search_published
from app.deps import get_db
from app.schemas.search import SearchResponse

router = APIRouter()


@router.get("", response_model=SearchResponse, summary="全站搜索（仅已发布文章）")
def search(
    q: str = Query("", description="搜索关键词（空格分隔多个词，按词 AND）"),
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return search_published(db, q, page, size)
