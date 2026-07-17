"""slug 生成与去重。保留字母数字与 CJK，其余转连字符。"""

import re

from sqlalchemy.orm import Session

from app.models.article import Article

_NON_SLUG = re.compile(r"[^\w一-鿿]+", re.UNICODE)
_DASHES = re.compile(r"-+")


def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    text = _NON_SLUG.sub("-", text)
    text = _DASHES.sub("-", text).strip("-_")
    return text or "article"


def unique_slug(
    db: Session, base: str, exclude_id: int | None = None, _model=Article
) -> str:
    base = slugify(base)
    candidate = base
    i = 2
    while True:
        q = db.query(_model).filter(_model.slug == candidate)
        if exclude_id is not None:
            q = q.filter(_model.id != exclude_id)
        if q.first() is None:
            return candidate
        candidate = f"{base}-{i}"
        i += 1
