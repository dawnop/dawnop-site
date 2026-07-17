"""标签接口：公开标签列表/单标签、后台标签管理（改名、合并、删除、清理），
以及供文章模块复用的「标签名 → 实体」解析。

路由声明顺序：静态路径（/admin、/merge、/cleanup）须先于 GET /{slug} 声明。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.crud import get_or_404
from app.core.slug import unique_slug
from app.deps import get_current_user, get_db
from app.models.article import Article
from app.models.tag import Tag, article_tags
from app.models.user import User
from app.schemas.tag import TagMerge, TagOut, TagUpdate, TagWithCount

router = APIRouter()

MAX_TAGS = 20  # 单篇标签数上限，防滥用


def resolve_tags(db: Session, names: list[str]) -> list[Tag]:
    """把标签名列表解析成 Tag 实体：按名（忽略大小写）取或建；去空、去重、截断。"""
    result: list[Tag] = []
    seen: set[str] = set()
    for raw in names or []:
        name = (raw or "").strip()[:64]
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        tag = db.query(Tag).filter(func.lower(Tag.name) == key).first()
        if tag is None:
            tag = Tag(name=name, slug=unique_slug(db, name, _model=Tag))
            db.add(tag)
            db.flush()
        result.append(tag)
        if len(result) >= MAX_TAGS:
            break
    return result


def _published_count(db: Session, tag_id: int) -> int:
    return (
        db.query(func.count(Article.id))
        .join(article_tags, article_tags.c.article_id == Article.id)
        .filter(article_tags.c.tag_id == tag_id, Article.published.is_(True))
        .scalar()
        or 0
    )


@router.get(
    "",
    response_model=list[TagWithCount],
    summary="公开标签列表（仅含已发布文章的标签，按文章数降序）",
)
def list_tags(db: Session = Depends(get_db)):
    rows = (
        db.query(Tag, func.count(Article.id).label("cnt"))
        .join(article_tags, article_tags.c.tag_id == Tag.id)
        .join(
            Article,
            (Article.id == article_tags.c.article_id) & Article.published.is_(True),
        )
        .group_by(Tag.id)
        .order_by(func.count(Article.id).desc(), Tag.name)
        .all()
    )
    return [
        TagWithCount(id=t.id, name=t.name, slug=t.slug, count=cnt) for t, cnt in rows
    ]


# ---------- 受保护：后台管理 ----------


@router.get(
    "/admin",
    response_model=list[TagWithCount],
    summary="后台全量标签（含文章数，草稿也计入）",
)
def list_all_tags(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    rows = (
        db.query(Tag, func.count(article_tags.c.article_id).label("cnt"))
        .outerjoin(article_tags, article_tags.c.tag_id == Tag.id)
        .group_by(Tag.id)
        .order_by(Tag.name)
        .all()
    )
    return [
        TagWithCount(id=t.id, name=t.name, slug=t.slug, count=cnt) for t, cnt in rows
    ]


@router.put("/{tag_id}", response_model=TagOut, summary="重命名标签（slug 联动重生成）")
def rename_tag(
    tag_id: int,
    payload: TagUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    tag = get_or_404(db, Tag, tag_id, "标签不存在")
    name = payload.name.strip()[:64]
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "标签名不能为空")
    dup = (
        db.query(Tag)
        .filter(func.lower(Tag.name) == name.lower(), Tag.id != tag.id)
        .first()
    )
    if dup:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"已存在同名标签「{dup.name}」，如需归并请用合并功能",
        )
    if name != tag.name:
        tag.name = name
        tag.slug = unique_slug(db, name, exclude_id=tag.id, _model=Tag)
        db.commit()
        db.refresh(tag)
    return tag


@router.post(
    "/merge",
    response_model=TagWithCount,
    summary="合并标签：source 的文章并入 target，删除 source",
)
def merge_tags(
    payload: TagMerge,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if payload.source_id == payload.target_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能与自身合并")
    source = get_or_404(db, Tag, payload.source_id, "源标签不存在")
    target = get_or_404(db, Tag, payload.target_id, "目标标签不存在")
    # 只补挂尚未带 target 的文章，避免关联表复合主键冲突
    has_target = {
        r[0]
        for r in db.query(article_tags.c.article_id).filter(
            article_tags.c.tag_id == target.id
        )
    }
    moving = [
        r[0]
        for r in db.query(article_tags.c.article_id).filter(
            article_tags.c.tag_id == source.id
        )
    ]
    for article_id in moving:
        if article_id not in has_target:
            db.execute(
                article_tags.insert().values(article_id=article_id, tag_id=target.id)
            )
    db.execute(article_tags.delete().where(article_tags.c.tag_id == source.id))
    db.delete(source)
    db.commit()
    cnt = (
        db.query(func.count(article_tags.c.article_id))
        .filter(article_tags.c.tag_id == target.id)
        .scalar()
        or 0
    )
    return TagWithCount(id=target.id, name=target.name, slug=target.slug, count=cnt)


@router.post("/cleanup", summary="清理未被任何文章使用的标签")
def cleanup_tags(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    used = db.query(article_tags.c.tag_id).distinct()
    orphans = db.query(Tag).filter(~Tag.id.in_(used)).all()
    for tag in orphans:
        db.delete(tag)
    db.commit()
    return {"deleted": len(orphans)}


@router.delete(
    "/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除标签（解除所有文章关联）",
)
def delete_tag(
    tag_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    tag = get_or_404(db, Tag, tag_id, "标签不存在")
    # 显式清关联行：SQLite 默认不开外键级联，不能依赖 ondelete=CASCADE
    db.execute(article_tags.delete().where(article_tags.c.tag_id == tag.id))
    db.delete(tag)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- 公开：按 slug 取单个（须放在最后，避免吞掉 /admin 等静态路径）----------


@router.get(
    "/{slug}",
    response_model=TagWithCount,
    summary="按 slug 取单个标签（公开，count 为已发布文章数）",
)
def get_tag(slug: str, db: Session = Depends(get_db)):
    tag = db.query(Tag).filter(Tag.slug == slug).first()
    if tag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "标签不存在")
    return TagWithCount(
        id=tag.id, name=tag.name, slug=tag.slug, count=_published_count(db, tag.id)
    )
