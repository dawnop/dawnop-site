"""初始化管理员账号。

用法（在 backend/ 目录，已激活 venv 并配置好 .env）：
    python scripts/seed_admin.py

账号取自 .env 的 ADMIN_USERNAME / ADMIN_PASSWORD。已存在则跳过。
"""

import json
import sys
from pathlib import Path

# 允许以 `python scripts/seed_admin.py` 直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.models.page import Page  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.viz import VizComponent  # noqa: E402


def _seed_admin(db) -> None:
    existing = db.query(User).filter(User.username == settings.admin_username).first()
    if existing:
        print(f"管理员 '{settings.admin_username}' 已存在，跳过。")
        return
    if settings.admin_password in ("", "change-me"):
        print("警告：ADMIN_PASSWORD 未设置或仍为默认值，请在 .env 中修改后重试。")
        sys.exit(1)
    db.add(
        User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
        )
    )
    db.commit()
    print(f"已创建管理员 '{settings.admin_username}'。")


def _seed_pages(db) -> None:
    """首次运行建默认页面：一个文章列表页 + 一个关于内容页。"""
    if db.query(Page).count() > 0:
        print("已存在页面，跳过默认页面初始化。")
        return
    db.add_all(
        [
            Page(title="博客", slug="blog", type="article_list", nav_order=0),
            Page(
                title="关于",
                slug="about",
                type="content",
                content="## 关于\n\n你好，这里是 dawnop 的个人博客。",
                nav_order=1,
            ),
        ]
    )
    db.commit()
    print("已创建默认页面：博客（列表页）、关于（内容页）。")


def _seed_viz(db) -> None:
    """初始化内置可视化组件（种子数据由 frontend/scripts/gen-viz-seed.mjs 生成）。

    已编译产物随 seed_viz.json 提交，部署只需跑本脚本、无需 Node。按 slug 幂等：已存在则跳过。
    """
    seed_file = Path(__file__).resolve().parent / "seed_viz.json"
    if not seed_file.exists():
        return
    items = json.loads(seed_file.read_text(encoding="utf-8"))
    created = 0
    for it in items:
        if db.query(VizComponent).filter(VizComponent.slug == it["slug"]).first():
            continue
        db.add(
            VizComponent(
                slug=it["slug"],
                name=it.get("name", ""),
                source=it["source"],
                compiled=it["compiled"],
                style=it.get("style", ""),
            )
        )
        created += 1
    if created:
        db.commit()
        print(f"已初始化 {created} 个内置可视化组件。")
    else:
        print("内置可视化组件已存在，跳过。")


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        _seed_admin(db)
        _seed_pages(db)
        _seed_viz(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
