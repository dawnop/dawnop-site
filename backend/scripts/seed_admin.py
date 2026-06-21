"""初始化管理员账号。

用法（在 backend/ 目录，已激活 venv 并配置好 .env）：
    python scripts/seed_admin.py

账号取自 .env 的 ADMIN_USERNAME / ADMIN_PASSWORD。已存在则跳过。
"""
import sys
from pathlib import Path

# 允许以 `python scripts/seed_admin.py` 直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.models.user import User  # noqa: E402


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        existing = (
            db.query(User).filter(User.username == settings.admin_username).first()
        )
        if existing:
            print(f"管理员 '{settings.admin_username}' 已存在，跳过。")
            return
        if settings.admin_password in ("", "change-me"):
            print("警告：ADMIN_PASSWORD 未设置或仍为默认值，请在 .env 中修改后重试。")
            sys.exit(1)
        user = User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
        )
        db.add(user)
        db.commit()
        print(f"已创建管理员 '{settings.admin_username}'。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
