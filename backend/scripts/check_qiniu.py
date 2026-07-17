"""七牛连通性自检：上传 → 签私有 URL → 下载校验 → 删除 一个临时对象。

用法（backend/ 下，已激活 venv 且 .env 配好七牛）：
    python scripts/check_qiniu.py

不打印任何密钥；仅用于验证 AK/SK/Bucket/Domain 配置与网络是否可用。
"""

import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.core import qiniu_client  # noqa: E402

KEY = "__smoketest__/check.txt"
PAYLOAD = b"dawnop qiniu connectivity check"


def main() -> None:
    print(f"bucket = {settings.qiniu_bucket}")
    print(f"domain = {settings.qiniu_domain}")
    for name in ("qiniu_access_key", "qiniu_secret_key"):
        if not getattr(settings, name):
            print(f"[FAIL] {name} 未配置")
            sys.exit(1)

    print("1) 上传…", end=" ")
    qiniu_client.proxy_upload(KEY, PAYLOAD, "text/plain")
    print("OK")

    print("2) stat…", end=" ")
    info = qiniu_client.stat(KEY)
    print(f"OK (fsize={info.get('fsize')}, mime={info.get('mimeType')})")

    print("3) 私有 URL 下载校验…", end=" ")
    url = qiniu_client.private_url(KEY, expires=120)
    with urllib.request.urlopen(url, timeout=15) as resp:
        body = resp.read()
    assert body == PAYLOAD, "下载内容与上传不一致"
    print("OK")

    print("4) 删除…", end=" ")
    qiniu_client.delete(KEY)
    print("OK")

    print("\n[PASS] 七牛配置与连通性正常。")


if __name__ == "__main__":
    main()
