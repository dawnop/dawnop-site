"""清空七牛空间内的全部对象（危险操作，不可逆）。

用法（backend/ 下，已激活 venv 且 .env 配好七牛）：
    python scripts/wipe_qiniu.py            # 列出数量并要求确认
    python scripts/wipe_qiniu.py --yes      # 跳过确认，直接删除
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qiniu import build_batch_delete  # noqa: E402

from app.config import settings  # noqa: E402
from app.core import qiniu_client  # noqa: E402


def _all_keys(bucket_mgr, bucket: str) -> list[str]:
    keys: list[str] = []
    marker = None
    while True:
        ret, eof, info = bucket_mgr.list(bucket, marker=marker, limit=1000)
        if ret is None:
            raise RuntimeError(f"列举失败: {info}")
        keys.extend(item["key"] for item in ret.get("items", []))
        marker = ret.get("marker")
        if not marker:
            break
    return keys


def main() -> None:
    bucket = settings.qiniu_bucket
    mgr = qiniu_client._bucket_manager()
    keys = _all_keys(mgr, bucket)
    print(f"空间 '{bucket}' 共有 {len(keys)} 个对象。")
    if not keys:
        print("无需删除。")
        return

    if "--yes" not in sys.argv:
        ans = input("确认全部删除？输入 yes 继续：").strip()
        if ans != "yes":
            print("已取消。")
            return

    deleted = 0
    for i in range(0, len(keys), 1000):
        chunk = keys[i : i + 1000]
        ops = build_batch_delete(bucket, chunk)
        ret, info = mgr.batch(ops)
        if info.status_code != 200:
            raise RuntimeError(f"批量删除失败: {info}")
        deleted += len(chunk)
    print(f"已删除 {deleted} 个对象。")


if __name__ == "__main__":
    main()
