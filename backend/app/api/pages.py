"""页面接口：公开导航/页面读取 + 受保护的页面管理。

路由顺序：静态路径（/nav、/admin、/reorder）须先于 GET /{slug} 声明。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.slug import unique_slug
from app.deps import get_current_user, get_db
from app.models.article import Article
from app.models.page import Page
from app.models.user import User
from app.schemas.article import ArticleListResponse
from app.schemas.page import NavItem, PageCreate, PageOut, PageUpdate, ReorderRequest

router = APIRouter()


def _get_or_404(db: Session, page_id: int) -> Page:
    page = db.get(Page, page_id)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "页面不存在")
    return page


# ---------- 公开 ----------


@router.get("/nav", response_model=list[NavItem])
def nav(db: Session = Depends(get_db)):
    return (
        db.query(Page)
        .filter(Page.nav_visible.is_(True))
        .order_by(Page.nav_order, Page.id)
        .all()
    )


# ---------- 受保护：管理 ----------


@router.get("/admin", response_model=list[PageOut])
def list_all(
    db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    return db.query(Page).order_by(Page.nav_order, Page.id).all()


@router.post("", response_model=PageOut, status_code=status.HTTP_201_CREATED)
def create_page(
    payload: PageCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    page = Page(
        title=payload.title,
        slug=unique_slug(db, payload.slug or payload.title, _model=Page),
        type=payload.type,
        description=payload.description,
        content=payload.content,
        nav_visible=payload.nav_visible,
        nav_order=payload.nav_order,
    )
    db.add(page)
    db.commit()
    db.refresh(page)
    return page


@router.post("/reorder", response_model=list[PageOut])
def reorder(
    payload: ReorderRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    for order, pid in enumerate(payload.ids):
        page = db.get(Page, pid)
        if page is not None:
            page.nav_order = order
    db.commit()
    return db.query(Page).order_by(Page.nav_order, Page.id).all()


@router.put("/{page_id}", response_model=PageOut)
def update_page(
    page_id: int,
    payload: PageUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    page = _get_or_404(db, page_id)
    data = payload.model_dump(exclude_unset=True)
    if "slug" in data:
        base = data["slug"] or data.get("title") or page.title
        page.slug = unique_slug(db, base, exclude_id=page.id, _model=Page)
        data.pop("slug")
    for key, value in data.items():
        setattr(page, key, value)
    db.commit()
    db.refresh(page)
    return page


@router.delete("/{page_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_page(
    page_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    page = _get_or_404(db, page_id)
    # 解除文章对本页的归属
    db.query(Article).filter(Article.page_id == page.id).update(
        {Article.page_id: None}
    )
    db.delete(page)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- 公开：按 slug 取页面 / 列表页文章（放在最后）----------


@router.get("/{slug}", response_model=PageOut)
def get_page(slug: str, db: Session = Depends(get_db)):
    page = db.query(Page).filter(Page.slug == slug).first()
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "页面不存在")
    return page


@router.get("/{slug}/articles", response_model=ArticleListResponse)
def list_page_articles(
    slug: str,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    page_obj = db.query(Page).filter(Page.slug == slug).first()
    if page_obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "页面不存在")

    query = db.query(Article).filter(
        Article.page_id == page_obj.id, Article.published.is_(True)
    )
    total = query.count()
    items = (
        query.order_by(Article.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return ArticleListResponse(total=total, page=page, size=size, items=items)
