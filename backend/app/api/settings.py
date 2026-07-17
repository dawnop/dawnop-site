"""后台全局配置（/api/settings，需鉴权）。

key-value 存 settings 表（JSON 编码），读取时与 DEFAULTS 合并——
库里只落被改过的项，新增配置项无需迁移。校验集中在 VALIDATORS。
"""

import json

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings as env_settings
from app.deps import get_current_user, get_db
from app.models.setting import Setting
from app.schemas.settings import SettingsOut

router = APIRouter()

# 默认值（storage_quota_gb 以 .env 为兜底，其余为代码默认）
DEFAULTS = {
    # 文件管理：上传/下载并发数
    "upload_concurrency": 3,
    "download_concurrency": 3,
    # 存储配额（GB），用量条分母（对应七牛「存储资源包」容量）
    "storage_quota_gb": env_settings.storage_quota_gb,
    # 文本在线预览/编辑的大小上限（KB），超过只给下载
    "text_preview_max_kb": 512,
}


def _int_range(lo: int, hi: int):
    def check(v):
        if not isinstance(v, int) or isinstance(v, bool) or not (lo <= v <= hi):
            raise ValueError(f"应为 {lo}~{hi} 的整数")
        return v

    return check


VALIDATORS = {
    "upload_concurrency": _int_range(1, 8),
    "download_concurrency": _int_range(1, 8),
    "storage_quota_gb": _int_range(1, 1024),
    "text_preview_max_kb": _int_range(16, 10240),
}


def get_all_settings(db: Session) -> dict:
    merged = dict(DEFAULTS)
    for row in db.query(Setting).all():
        if row.key in DEFAULTS:
            try:
                merged[row.key] = json.loads(row.value)
            except ValueError:
                pass  # 脏数据回落默认值
    return merged


def get_setting(db: Session, key: str):
    row = db.get(Setting, key)
    if row is not None:
        try:
            return json.loads(row.value)
        except ValueError:
            pass
    return DEFAULTS[key]


@router.get("", response_model=SettingsOut, summary="读取全局配置")
def read_settings(
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    return get_all_settings(db)


@router.put("", response_model=SettingsOut, summary="更新全局配置")
def update_settings(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    for key, value in payload.items():
        if key not in DEFAULTS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"未知配置项：{key}")
        try:
            value = VALIDATORS[key](value)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{key} {e}") from e
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=json.dumps(value)))
        else:
            row.value = json.dumps(value)
    db.commit()
    return get_all_settings(db)
