"""清理七牛空间里的孤儿对象（存在于空间、但当前 files 表没有登记的 key）。

孤儿来源：前端直传成功但 /register 未完成（网络中断、测试脚本被杀等）。
只删「不在数据库里的 key」，登记过的文件绝不会被碰。

⚠️ 必须在该空间对应的**权威数据库**环境下执行：本地开发库和生产库共用同一个
七牛空间时，本地跑会把「生产登记、本地没有」的正常文件误判成孤儿——
生产空间的清理只能登录生产服务器、用生产 .env + 生产库跑。
--delete 前务必先干跑核对清单。

用法（backend/ 下，已激活 venv 且 .env 配好七牛）：
    python scripts/sweep_qiniu_orphans.py           # 干跑：只列出孤儿，不删除
    python scripts/sweep_qiniu_orphans.py --delete  # 确认后删除
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qiniu import build_batch_delete  # noqa: E402

from app.config import settings  # noqa: E402
from app.core import qiniu_client  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.file_object import FileObject  # noqa: E402


def _all_remote(bucket_mgr, bucket: str) -> dict[str, int]:
    """空间内全部对象：key -> fsize。"""
    items: dict[str, int] = {}
    marker = None
    while True:
        ret, eof, info = bucket_mgr.list(bucket, marker=marker, limit=1000)
        if ret is None:
            raise RuntimeError(f"列举失败: {info}")
        for it in ret.get("items", []):
            items[it["key"]] = it.get("fsize", 0)
        marker = ret.get("marker")
        if not marker:
            break
    return items


def main() -> None:
    bucket = settings.qiniu_bucket
    mgr = qiniu_client._bucket_manager()
    remote = _all_remote(mgr, bucket)

    db = SessionLocal()
    try:
        known = {
            k for (k,) in db.query(FileObject.key).filter(FileObject.key.isnot(None))
        }
    finally:
        db.close()

    orphans = {k: sz for k, sz in remote.items() if k not in known}
    total = sum(orphans.values())
    print(f"空间 '{bucket}'：共 {len(remote)} 个对象，登记 {len(known)} 个，"
          f"孤儿 {len(orphans)} 个（{total / 1024 / 1024:.1f} MB）。")
    for k, sz in sorted(orphans.items()):
        print(f"  {k}  {sz}")
    if not orphans:
        return

    if "--delete" not in sys.argv:
        print("\n干跑模式：未删除。确认无误后加 --delete 执行。")
        return
    ans = input(f"确认删除以上 {len(orphans)} 个孤儿对象？输入 yes 继续：").strip()
    if ans != "yes":
        print("已取消。")
        return

    keys = list(orphans)
    for i in range(0, len(keys), 1000):
        ops = build_batch_delete(bucket, keys[i : i + 1000])
        ret, info = mgr.batch(ops)
        if info.status_code != 200:
            raise RuntimeError(f"批量删除失败: {info}")
    print(f"已删除 {len(keys)} 个孤儿对象。")


if __name__ == "__main__":
    main()
