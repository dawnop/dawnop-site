"""标签接口：公开标签列表（含已发布文章数）、后台全量标签，
以及供文章模块复用的「标签名 → 实体」解析。"""
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.slug import unique_slug
from app.deps import get_current_user, get_db
from app.models.article import Article
from app.models.tag import Tag, article_tags
from app.models.user import User
from app.schemas.tag import TagOut, TagWithCount

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


@router.get("", response_model=list[TagWithCount], summary="公开标签列表（仅含已发布文章的标签，按文章数降序）")
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
    return [TagWithCount(id=t.id, name=t.name, slug=t.slug, count=cnt) for t, cnt in rows]


@router.get("/admin", response_model=list[TagOut], summary="后台全量标签（供编辑器补全）")
def list_all_tags(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return db.query(Tag).order_by(Tag.name).all()
