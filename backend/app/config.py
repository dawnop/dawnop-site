"""应用配置：从 backend/.env 读取，集中管理敏感信息与可调参数。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "dawnop-site"

    # 鉴权
    secret_key: str = "dev-insecure-secret-change-me-before-deploying-to-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    # 数据库
    database_url: str = "sqlite:///./dawnop.db"

    # 中文分词扩展（wangfenjin/simple）路径，可不带后缀（sqlite 自动补 .so/.dylib）。
    # 留空则用默认 backend/extensions/libsimple；文件不存在时静默跳过、搜索退回 trigram/LIKE。
    simple_extension_path: str = ""

    # CORS：逗号分隔的来源列表
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 七牛云 Kodo
    qiniu_access_key: str = ""
    qiniu_secret_key: str = ""
    qiniu_bucket: str = ""
    qiniu_domain: str = ""
    qiniu_token_expires: int = 3600

    # 初始管理员（仅 seed_admin.py 使用）
    admin_username: str = "admin"
    admin_password: str = "change-me"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
