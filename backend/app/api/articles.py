"""文章接口：公开只读 + 受保护的增删改、Markdown 导入/导出。

路由声明顺序很重要：静态路径（/admin、/import）与带前缀的整型路径需先于
GET /{slug} 声明，避免被 slug 通配吞掉。
"""
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi import status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.crud import drop_null_created_at, get_or_404
from app.core.pagination import paginate
from app.core.slug import slugify, unique_slug
from app.deps import get_current_user, get_db
from app.models.article import Article
from app.models.page import Page
from app.models.user import User
from app.schemas.article import (
    ArticleCreate,
    ArticleListResponse,
    ArticleOut,
    ArticleUpdate,
)

router = APIRouter()


def _get_or_404(db: Session, article_id: int) -> Article:
    return get_or_404(db, Article, article_id, "文章不存在")


def _validate_page_ref(db: Session, page_id: int | None) -> None:
    """文章只能归属到「文章列表页」。传了 page_id 就校验其存在且类型正确。"""
    if page_id is None:
        return
    page = db.get(Page, page_id)
    if page is None or page.type != "article_list":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "所属页面不存在或不是文章列表页"
        )


# ---------- 公开只读 ----------


@router.get("", response_model=ArticleListResponse, summary="公开文章列表（仅已发布）")
def list_published(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = (
        db.query(Article)
        .filter(Article.published.is_(True))
        .order_by(Article.created_at.desc())
    )
    return paginate(query, page, size)


# ---------- 受保护：管理列表 / 单篇（含草稿）----------


@router.get("/admin", response_model=ArticleListResponse, summary="管理文章列表（含草稿，可筛选）")
def list_all(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    published: bool | None = Query(None, description="按状态筛选：true=已发布 false=草稿 省略=全部"),
    page_id: int | None = Query(None, description="按所属列表页筛选"),
    q: str | None = Query(None, description="按标题模糊搜索"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = db.query(Article)
    if published is not None:
        query = query.filter(Article.published.is_(published))
    if page_id is not None:
        query = query.filter(Article.page_id == page_id)
    if q and q.strip():
        query = query.filter(Article.title.ilike(f"%{q.strip()}%"))
    query = query.order_by(Article.created_at.desc())
    return paginate(query, page, size)


@router.get("/admin/{article_id}", response_model=ArticleOut, summary="取单篇（编辑用，含草稿）")
def get_for_edit(
    article_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return _get_or_404(db, article_id)


# ---------- 受保护：增删改 ----------


@router.post("", response_model=ArticleOut, status_code=status.HTTP_201_CREATED, summary="创建文章")
def create_article(
    payload: ArticleCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    _validate_page_ref(db, payload.page_id)
    article = Article(
        title=payload.title,
        slug=unique_slug(db, payload.slug or payload.title),
        summary=payload.summary,
        content=payload.content,
        published=payload.published,
        page_id=payload.page_id,
    )
    if payload.created_at is not None:
        article.created_at = payload.created_at
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


@router.put("/{article_id}", response_model=ArticleOut, summary="更新文章")
def update_article(
    article_id: int,
    payload: ArticleUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    article = _get_or_404(db, article_id)
    data = drop_null_created_at(payload.model_dump(exclude_unset=True))

    if "page_id" in data:
        _validate_page_ref(db, data["page_id"])

    if "slug" in data:
        # 显式传 slug 时重算唯一 slug；以 slug 优先，否则用新/旧标题
        base = data["slug"] or data.get("title") or article.title
        article.slug = unique_slug(db, base, exclude_id=article.id)
        data.pop("slug")

    for key, value in data.items():
        setattr(article, key, value)

    db.commit()
    db.refresh(article)
    return article


@router.delete("/{article_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除文章")
def delete_article(
    article_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    article = _get_or_404(db, article_id)
    db.delete(article)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- 受保护：Markdown 导入 / 导出 ----------


@router.post(
    "/import",
    response_model=ArticleOut,
    status_code=status.HTTP_201_CREATED,
    summary="导入 Markdown 文件为文章",
)
async def import_markdown(
    file: UploadFile = File(...),
    published: bool = Form(False),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    try:
        raw = (await file.read()).decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件不是 UTF-8 文本，无法导入")
    stem = Path(file.filename).stem if file.filename else ""

    # 标题优先取首个 H1，否则用文件名
    title = stem or "untitled"
    lines = raw.splitlines()
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip() or title

    article = Article(
        title=title,
        slug=unique_slug(db, slugify(stem or title)),
        content=raw,
        summary="",
        published=published,
    )
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


@router.get("/{article_id}/export", summary="导出文章为 Markdown 文件")
def export_markdown(
    article_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    article = _get_or_404(db, article_id)
    filename = f"{article.slug}.md"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=article.content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


# ---------- 公开：按 slug 取单篇（须放在最后）----------


@router.get("/{slug}", response_model=ArticleOut, summary="按 slug 取已发布文章")
def get_published(slug: str, db: Session = Depends(get_db)):
    article = (
        db.query(Article)
        .filter(Article.slug == slug, Article.published.is_(True))
        .first()
    )
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文章不存在")
    return article
