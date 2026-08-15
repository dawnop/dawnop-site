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

    # 回滚探针（GET /api/rollback/db-identity）的共享秘密，随 X-Rollback-Probe 头出示。
    # 留空 = 探针整体关闭（任何请求都 404），本地开发与测试就是这个状态。
    rollback_probe_header: str = ""

    # 七牛云 Kodo
    qiniu_access_key: str = ""
    qiniu_secret_key: str = ""
    qiniu_bucket: str = ""
    qiniu_domain: str = ""
    qiniu_token_expires: int = 3600
    # 存储配额（GB），仅文件管理侧栏用量条展示用；七牛标准存储免费额度 10GB
    storage_quota_gb: int = 10

    # 腾讯云（监控页用）：只读子账号密钥。留空则监控页「腾讯云」块降级为仅本机指标。
    # region 是账号级的偏好，留个默认值无害；instance_id 是这台机器的身份，没有
    # 「通用的」值可言，故只能由 .env 提供（模板见 .env.example）。Dawn 侧同样默认空。
    tencent_secret_id: str = ""
    tencent_secret_key: str = ""
    tencent_region: str = "ap-shanghai"
    lighthouse_instance_id: str = ""

    # Vaultwarden 存活探测（监控页「Vault」块）。后端与容器同机，走内网 127.0.0.1。
    vault_alive_url: str = "http://127.0.0.1:8222/alive"
    # Vault 公网入口（监控页「打开 Vault」按钮跳转），供浏览器访问的地址
    vault_public_url: str = "https://vault.dawnop.com"

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
