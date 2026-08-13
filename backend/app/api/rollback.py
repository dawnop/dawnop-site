"""回滚探针：让回滚脚本能问一句「你现在连的是哪个库」。

只在回滚链里被调用（`backend-dawn/deploy/rollback-to-fastapi.sh` 与 `return-to-dawn.sh`），
不进 OpenAPI 文档，也不打算给浏览器用。

**不带正确的头就回 404，不是 403**：403 等于确认这个端点存在。它是一个专门用来暴露进程
内部状态的端点，对没出示凭据的人来说，最好的答案是「这里什么都没有」。
"""

import hmac

from fastapi import APIRouter, HTTPException, Request, status

from app.config import settings
from app.core.db_identity import (
    database_identity_latched,
    latched_database_fingerprint,
)
from app.core.process_guard import process_guard_latched

router = APIRouter()

# 头名字是公开的；秘密在值里（.env 的 ROLLBACK_PROBE_HEADER）。
PROBE_HEADER_NAME = "X-Rollback-Probe"


def _probe_authorized(request: Request) -> bool:
    expected = settings.rollback_probe_header.strip()
    if not expected:
        # 没配秘密 = 探针整体关闭。空串绝不能被当成「谁都能进」。
        return False
    presented = request.headers.get(PROBE_HEADER_NAME, "")
    return hmac.compare_digest(presented, expected)


@router.get("/db-identity", include_in_schema=False)
def db_identity(request: Request) -> dict:
    if not _probe_authorized(request):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    if not database_identity_latched():
        # 未闩 = 这个进程还没核定过库身份。回一个指纹出去就是在编，
        # 而回滚脚本会把编出来的答案当成核对通过。
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="库身份尚未核定",
        )
    return {
        "status": "ok",
        "database_fingerprint": latched_database_fingerprint(),
        "process_guard_latched": process_guard_latched(),
    }
