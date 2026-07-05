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
    # 存储配额（GB），仅文件管理侧栏用量条展示用；七牛标准存储免费额度 10GB
    storage_quota_gb: int = 10

    # 腾讯云（监控页用）：只读子账号密钥。留空则监控页「腾讯云」块降级为仅本机指标。
    # region/instance_id 为本站 Lighthouse 轻量服务器（DescribeInstances 探得），可被 .env 覆盖。
    tencent_secret_id: str = ""
    tencent_secret_key: str = ""
    tencent_region: str = "ap-shanghai"
    lighthouse_instance_id: str = "lhins-8clkew6k"

    # Vaultwarden 存活探测（监控页「Vault」块）。后端与容器同机，走内网 127.0.0.1。
    vault_alive_url: str = "http://127.0.0.1:8222/alive"

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
