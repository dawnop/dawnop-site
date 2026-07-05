"""七牛云封装（`app.core.qiniu_client`）。

历史是单文件，现按职责拆包，对外接口不变（调用方仍 `from app.core import qiniu_client`
再 `qiniu_client.xxx()`；tests 仍 `monkeypatch.setattr(qiniu_client, "delete", ...)`）：
- kodo   对象操作：上传凭证 / 代理上传 / 私有 URL / stat / 删除 / 复制
- stats  Kodo 存储·流量统计（space/count/flow/bucket_space）
- cdn    融合 CDN 用量 + 资源包（流量包/存储包）余额

所有函数失败抛 RuntimeError，由上层转 HTTP 错误。
"""
from .kodo import (  # noqa: F401
    copy,
    delete,
    private_url,
    proxy_upload,
    proxy_upload_file,
    stat,
    upload_host,
    upload_token,
)
from .stats import (  # noqa: F401
    _last_nonzero,
    bucket_space,
    count_series,
    flow_series,
    space_series,
)
from .cdn import (  # noqa: F401
    cdn_bandwidth_peak,
    cdn_domains,
    cdn_flux_series,
    respack_summary,
)
