"""文章接口：公开只读 + 受保护的增删改、Markdown 导出。

路由声明顺序很重要：静态路径（/admin）与带前缀的整型路径需先于
GET /{slug} 声明，避免被 slug 通配吞掉。
"""

import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.tags import resolve_tags
from app.core.crud import drop_null_created_at, get_or_404
from app.core.pagination import paginate
from app.core.slug import unique_slug
from app.deps import get_current_user, get_current_user_optional, get_db
from app.models.article import Article
from app.models.page import Page
from app.models.tag import Tag
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
    tag: str | None = Query(None, description="按标签 slug 筛选"),
    db: Session = Depends(get_db),
):
    query = db.query(Article).filter(Article.published.is_(True))
    if tag:
        query = query.filter(Article.tags.any(Tag.slug == tag))
    query = query.order_by(Article.created_at.desc())
    return paginate(query, page, size)


# ---------- 受保护：管理列表 / 单篇（含草稿）----------


@router.get(
    "/admin",
    response_model=ArticleListResponse,
    summary="管理文章列表（含草稿，可筛选）",
)
def list_all(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    published: bool | None = Query(
        None, description="按状态筛选：true=已发布 false=草稿 省略=全部"
    ),
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


@router.get("/admin/stats", summary="后台统计（文章数/草稿/总浏览量）")
def admin_stats(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    total = db.query(func.count(Article.id)).scalar() or 0
    published = (
        db.query(func.count(Article.id)).filter(Article.published.is_(True)).scalar()
        or 0
    )
    total_views = db.query(func.coalesce(func.sum(Article.views), 0)).scalar() or 0
    return {
        "total": total,
        "published": published,
        "drafts": total - published,
        "total_views": int(total_views),
    }


@router.get(
    "/admin/{article_id}", response_model=ArticleOut, summary="取单篇（编辑用，含草稿）"
)
def get_for_edit(
    article_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return _get_or_404(db, article_id)


# ---------- 受保护：增删改 ----------


@router.post(
    "",
    response_model=ArticleOut,
    status_code=status.HTTP_201_CREATED,
    summary="创建文章",
)
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
        auto_title=payload.auto_title,
        page_id=payload.page_id,
    )
    article.tags = resolve_tags(db, payload.tags)
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

    if "tags" in data:
        article.tags = resolve_tags(db, data.pop("tags") or [])

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


@router.delete(
    "/{article_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除文章"
)
def delete_article(
    article_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    article = _get_or_404(db, article_id)
    db.delete(article)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- 受保护：Markdown 导出 ----------
# 导入已改为前端读取文件 + 复用「新建文章」流程（更便于预览/微调），故不再提供导入接口。


def _yaml_scalar(s: str) -> str:
    """极简标量序列化：含特殊字符或首尾空白就双引号包裹转义，否则裸写。"""
    if (
        s == ""
        or s[0] in " -"
        or s[-1] == " "
        or re.search(r"""[:#\[\]{}>|*&!%@`"'\n]""", s)
    ):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _build_frontmatter(a: Article) -> str:
    """从文章字段反生成 YAML front matter（与前端 parseFrontmatter 对称）。
    auto_title 时标题取自正文 H1，故不写 title——保证「导出→导入」闭环不丢失该状态。
    """
    lines = []
    if not a.auto_title:
        lines.append(f"title: {_yaml_scalar(a.title)}")
    if a.summary:
        lines.append(f"summary: {_yaml_scalar(a.summary)}")
    lines.append(f"slug: {a.slug}")
    if a.tags:
        lines.append("tags: [" + ", ".join(_yaml_scalar(t.name) for t in a.tags) + "]")
    lines.append(f"published: {'true' if a.published else 'false'}")
    return "---\n" + "\n".join(lines) + "\n---\n\n"


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
        content=_build_frontmatter(article) + article.content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


# ---------- 公开：按 slug 取单篇（须放在最后）----------


@router.get(
    "/{slug}",
    response_model=ArticleOut,
    summary="按 slug 取单篇（已发布公开；草稿仅登录可见）",
)
def get_published(
    slug: str,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    """草稿不进公开列表，但管理员凭直链（带有效 token）可直接预览；匿名访问草稿仍 404。"""
    article = db.query(Article).filter(Article.slug == slug).first()
    if article is None or (not article.published and user is None):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文章不存在")
    # 仅统计匿名访客对已发布文章的访问（排除管理员带 token 的预览）
    if user is None and article.published:
        article.views = (article.views or 0) + 1
        db.commit()
        db.refresh(article)
    return article
