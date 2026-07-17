"""页面接口：公开导航/页面读取 + 受保护的页面管理。

路由顺序：静态路径（/nav、/admin、/reorder）须先于 GET /{slug} 声明。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.crud import drop_null_created_at, get_or_404
from app.core.pagination import paginate
from app.core.slug import unique_slug
from app.deps import get_current_user, get_db
from app.models.article import Article
from app.models.page import Page
from app.models.user import User
from app.schemas.article import ArticleListResponse
from app.schemas.page import NavItem, PageCreate, PageOut, PageUpdate, ReorderRequest

router = APIRouter()


def _get_or_404(db: Session, page_id: int) -> Page:
    return get_or_404(db, Page, page_id, "页面不存在")


def _get_by_slug_or_404(db: Session, slug: str) -> Page:
    page = db.query(Page).filter(Page.slug == slug).first()
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "页面不存在")
    return page


# ---------- 公开 ----------


@router.get("/nav", response_model=list[NavItem], summary="导航栏页面（公开）")
def nav(db: Session = Depends(get_db)):
    return (
        db.query(Page)
        .filter(Page.nav_visible.is_(True))
        .order_by(Page.nav_order, Page.id)
        .all()
    )


# ---------- 受保护：管理 ----------


@router.get("/admin", response_model=list[PageOut], summary="全部页面（管理）")
def list_all(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Page).order_by(Page.nav_order, Page.id).all()


@router.post(
    "", response_model=PageOut, status_code=status.HTTP_201_CREATED, summary="创建页面"
)
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
        auto_title=payload.auto_title,
        nav_visible=payload.nav_visible,
        nav_order=payload.nav_order,
    )
    if payload.created_at is not None:
        page.created_at = payload.created_at
    db.add(page)
    db.commit()
    db.refresh(page)
    return page


@router.post("/reorder", response_model=list[PageOut], summary="按 id 列表重排导航顺序")
def reorder(
    payload: ReorderRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    # 去重并保序，避免重复 id 造成的无意义写入
    seen: set[int] = set()
    ordered_ids: list[int] = []
    for pid in payload.ids:
        if pid not in seen:
            seen.add(pid)
            ordered_ids.append(pid)
    for order, pid in enumerate(ordered_ids):
        page = db.get(Page, pid)
        if page is not None:
            page.nav_order = order
    db.commit()
    return db.query(Page).order_by(Page.nav_order, Page.id).all()


@router.put("/{page_id}", response_model=PageOut, summary="更新页面")
def update_page(
    page_id: int,
    payload: PageUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    page = _get_or_404(db, page_id)
    data = drop_null_created_at(payload.model_dump(exclude_unset=True))
    if page.type == "builtin":
        # 内置页只放开导航相关字段；slug/type/content 是路由与渲染的根基，不可改
        data = {
            k: v for k, v in data.items() if k in {"title", "nav_visible", "nav_order"}
        }
    if "slug" in data:
        base = data["slug"] or data.get("title") or page.title
        page.slug = unique_slug(db, base, exclude_id=page.id, _model=Page)
        data.pop("slug")
    for key, value in data.items():
        setattr(page, key, value)
    db.commit()
    db.refresh(page)
    return page


@router.delete("/{page_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除页面")
def delete_page(
    page_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    page = _get_or_404(db, page_id)
    if page.type == "builtin":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "内置页面不可删除，可在导航中隐藏"
        )
    # 解除文章对本页的归属
    db.query(Article).filter(Article.page_id == page.id).update({Article.page_id: None})
    db.delete(page)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- 公开：按 slug 取页面 / 列表页文章（放在最后）----------


@router.get("/{slug}", response_model=PageOut, summary="按 slug 取页面（公开）")
def get_page(slug: str, db: Session = Depends(get_db)):
    page = _get_by_slug_or_404(db, slug)
    if page.type == "builtin":
        # 内置页是 SPA 固定路由，不作为内容页对外提供
        raise HTTPException(status.HTTP_404_NOT_FOUND, "页面不存在")
    return page


@router.get(
    "/{slug}/articles",
    response_model=ArticleListResponse,
    summary="列表页下的已发布文章",
)
def list_page_articles(
    slug: str,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    page_obj = _get_by_slug_or_404(db, slug)
    query = (
        db.query(Article)
        .filter(Article.page_id == page_obj.id, Article.published.is_(True))
        .order_by(Article.created_at.desc())
    )
    return paginate(query, page, size)
