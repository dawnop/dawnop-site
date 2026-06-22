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

from app.core.slug import slugify, unique_slug
from app.deps import get_current_user, get_db
from app.models.article import Article
from app.models.user import User
from app.schemas.article import (
    ArticleCreate,
    ArticleListResponse,
    ArticleOut,
    ArticleUpdate,
)

router = APIRouter()


def _get_or_404(db: Session, article_id: int) -> Article:
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文章不存在")
    return article


def _paginate(db: Session, page: int, size: int, published_only: bool):
    query = db.query(Article)
    if published_only:
        query = query.filter(Article.published.is_(True))
    total = query.count()
    items = (
        query.order_by(Article.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return {"total": total, "page": page, "size": size, "items": items}


# ---------- 公开只读 ----------


@router.get("", response_model=ArticleListResponse)
def list_published(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return _paginate(db, page, size, published_only=True)


# ---------- 受保护：管理列表 / 单篇（含草稿）----------


@router.get("/admin", response_model=ArticleListResponse)
def list_all(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return _paginate(db, page, size, published_only=False)


@router.get("/admin/{article_id}", response_model=ArticleOut)
def get_for_edit(
    article_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return _get_or_404(db, article_id)


# ---------- 受保护：增删改 ----------


@router.post("", response_model=ArticleOut, status_code=status.HTTP_201_CREATED)
def create_article(
    payload: ArticleCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    base = payload.slug or payload.title
    article = Article(
        title=payload.title,
        slug=unique_slug(db, base),
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


@router.put("/{article_id}", response_model=ArticleOut)
def update_article(
    article_id: int,
    payload: ArticleUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    article = _get_or_404(db, article_id)
    data = payload.model_dump(exclude_unset=True)

    # created_at 显式传 null 时忽略，避免把发布时间清空
    if data.get("created_at") is None:
        data.pop("created_at", None)

    if "slug" in data:
        # 显式传 slug（含空串）时重算唯一 slug；以 slug 优先，否则用新/旧标题
        base = data["slug"] or data.get("title") or article.title
        article.slug = unique_slug(db, base, exclude_id=article.id)
        data.pop("slug")

    for key, value in data.items():
        setattr(article, key, value)

    db.commit()
    db.refresh(article)
    return article


@router.delete("/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
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
    "/import", response_model=ArticleOut, status_code=status.HTTP_201_CREATED
)
async def import_markdown(
    file: UploadFile = File(...),
    published: bool = Form(False),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    raw = (await file.read()).decode("utf-8")
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


@router.get("/{article_id}/export")
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


@router.get("/{slug}", response_model=ArticleOut)
def get_published(slug: str, db: Session = Depends(get_db)):
    article = (
        db.query(Article)
        .filter(Article.slug == slug, Article.published.is_(True))
        .first()
    )
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文章不存在")
    return article
