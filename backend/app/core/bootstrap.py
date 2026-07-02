"""启动时的幂等数据引导。"""
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.page import BUILTIN_PAGES, Page


def ensure_builtin_pages(db: Session | None = None) -> None:
    """确保内置页（首页/标签页等）在 pages 表中存在，缺哪个补哪个。

    幂等：已存在（含用户页面恰好占用了同名 slug 的罕见情形）则不动，
    故用户对内置页的改名/显隐/排序不会被启动重置。
    """
    own = db is None
    if own:
        db = SessionLocal()
    try:
        for spec in BUILTIN_PAGES:
            if db.query(Page).filter(Page.slug == spec["slug"]).first() is not None:
                continue
            db.add(
                Page(
                    title=spec["title"],
                    slug=spec["slug"],
                    type="builtin",
                    nav_visible=spec.get("nav_visible", True),
                    nav_order=spec["nav_order"],
                )
            )
        db.commit()
    finally:
        if own:
            db.close()
