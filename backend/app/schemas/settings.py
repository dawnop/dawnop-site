"""全局配置响应模型。

字段与 app/api/settings.py 的 DEFAULTS 键一一对应；新增配置项时两处同步。
（请求仍用裸 dict：PUT 支持部分更新，且需保留「未知键 / 非法值 → 400」的自定义错误，
详见 settings.update_settings 的 VALIDATORS，故不套请求模型。）
"""

from pydantic import BaseModel


class SettingsOut(BaseModel):
    upload_concurrency: int
    download_concurrency: int
    storage_quota_gb: int
    text_preview_max_kb: int
