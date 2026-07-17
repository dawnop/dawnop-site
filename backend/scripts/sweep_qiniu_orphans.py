"""清理七牛空间里的孤儿对象（直传成功但从未 /register 登记的 key）。

孤儿来源：前端直传成功后、登记前中断（关页、断网、脚本被杀）。

两种模式：

  默认（账本模式，**共用空间也安全**）：只看 `pending_uploads` 账本——本环境
  亲自签发过上传凭证、超过时限却始终没登记成功的 key。绝不会碰共用空间里
  另一环境登记的对象。日常清理用这个。

  --full（全量比对，**危险**）：列举整个空间，减去本库 files 表登记的 key。
  本地开发库和生产库共用同一七牛空间时，本地跑会把「生产登记、本地没有」的
  正常文件误判成孤儿——仅当你确认本库是该空间的**唯一权威库**时才用，且只应
  在生产服务器上跑。留作清理「账本机制上线前」就已存在的历史孤儿。

--delete 前务必先干跑核对清单。

用法（backend/ 下，已激活 venv 且 .env 配好七牛）：
    python scripts/sweep_qiniu_orphans.py                    # 账本干跑
    python scripts/sweep_qiniu_orphans.py --delete           # 账本删除
    python scripts/sweep_qiniu_orphans.py --older-than 6     # 只清 6 小时前的
    python scripts/sweep_qiniu_orphans.py --full             # 全量比对干跑（危险）
    python scripts/sweep_qiniu_orphans.py --full --delete
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.core import qiniu_client  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.file_object import FileObject  # noqa: E402
from app.models.pending_upload import PendingUpload  # noqa: E402
from qiniu import build_batch_delete  # noqa: E402


def _all_remote(bucket_mgr, bucket: str) -> dict:
    """空间内全部对象：key -> fsize。"""
    items: dict = {}
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


def _delete_keys(bucket: str, mgr, keys: list) -> None:
    for i in range(0, len(keys), 1000):
        ops = build_batch_delete(bucket, keys[i : i + 1000])
        ret, info = mgr.batch(ops)
        if info.status_code != 200:
            raise RuntimeError(f"批量删除失败: {info}")


def _confirm(n: int) -> bool:
    ans = input(f"确认删除以上 {n} 个孤儿对象？输入 yes 继续：").strip()
    if ans != "yes":
        print("已取消。")
        return False
    return True


def sweep_ledger(bucket: str, mgr, older_than_h: float, do_delete: bool) -> None:
    """账本模式：清理本环境签发过但从未登记的过期 key（共用空间安全）。"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_h)
    db = SessionLocal()
    try:
        pendings = db.query(PendingUpload).all()
        known = {
            k for (k,) in db.query(FileObject.key).filter(FileObject.key.isnot(None))
        }
        stale_rows = []  # 已登记成功、账本残留 → 只清账本
        orphan_keys = []  # 过期未登记 → 真孤儿
        for p in pendings:
            if p.key in known:
                stale_rows.append(p.key)
                continue
            created = p.created_at
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created is None or created < cutoff:
                orphan_keys.append(p.key)

        print(
            f"空间 '{bucket}' 账本：待登记 {len(pendings)} 条，"
            f"其中已登记残留 {len(stale_rows)} 条、"
            f"过期未登记（孤儿）{len(orphan_keys)} 条（>{older_than_h}h）。"
        )
        for k in sorted(orphan_keys):
            print(f"  {k}")

        if stale_rows:
            # 登记已成功的残留账本行随手清掉（不碰七牛对象）
            db.query(PendingUpload).filter(PendingUpload.key.in_(stale_rows)).delete(
                synchronize_session=False
            )
            db.commit()
            print(f"（已清理 {len(stale_rows)} 条已登记的残留账本行）")

        if not orphan_keys:
            return
        if not do_delete:
            print("\n干跑模式：未删除。确认无误后加 --delete 执行。")
            return
        if not _confirm(len(orphan_keys)):
            return
        _delete_keys(bucket, mgr, orphan_keys)
        db.query(PendingUpload).filter(PendingUpload.key.in_(orphan_keys)).delete(
            synchronize_session=False
        )
        db.commit()
        print(f"已删除 {len(orphan_keys)} 个孤儿对象并清账本。")
    finally:
        db.close()


def sweep_full(bucket: str, mgr, do_delete: bool) -> None:
    """全量比对模式（危险，仅唯一权威库、生产环境用）。"""
    print(
        "⚠️  全量比对模式：仅当本库是该空间唯一权威库时可用（勿在共用空间的本地库跑）。\n"
    )
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
    print(
        f"空间 '{bucket}'：共 {len(remote)} 个对象，登记 {len(known)} 个，"
        f"孤儿 {len(orphans)} 个（{total / 1024 / 1024:.1f} MB）。"
    )
    for k, sz in sorted(orphans.items()):
        print(f"  {k}  {sz}")
    if not orphans:
        return
    if not do_delete:
        print("\n干跑模式：未删除。确认无误后加 --delete 执行。")
        return
    if not _confirm(len(orphans)):
        return
    _delete_keys(bucket, mgr, list(orphans))
    print(f"已删除 {len(orphans)} 个孤儿对象。")


def main() -> None:
    ap = argparse.ArgumentParser(description="清理七牛孤儿对象")
    ap.add_argument("--delete", action="store_true", help="确认后执行删除（默认干跑）")
    ap.add_argument(
        "--full", action="store_true", help="全量比对模式（危险，仅权威库）"
    )
    ap.add_argument(
        "--older-than",
        type=float,
        default=24.0,
        help="账本模式：只清早于该小时数的未登记 key（默认 24）",
    )
    args = ap.parse_args()

    bucket = settings.qiniu_bucket
    mgr = qiniu_client._bucket_manager()
    if args.full:
        sweep_full(bucket, mgr, args.delete)
    else:
        sweep_ledger(bucket, mgr, args.older_than, args.delete)


if __name__ == "__main__":
    main()
