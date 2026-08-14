"""`deploy/backup-db.sh` 的判词。

备份脚本和回滚脚本是一路货：平时没人跑，等到有人跑的那天已经出事了。所以这里
不测「脚本的某个函数」，而是**真的把它跑起来**，喂真的 sqlite 库，看落地的产物。

每条判词都有一个能让它转红的 production mutant，见 `scripts/backup_db_mutants.py`。
2026-08-13 那次回滚演练改坏了十篇文章的 `updated_at`，全机器没有一份备份可回滚，
这套东西就是那件事的补课。
"""

import gzip
import hashlib
import os
import re
import shutil
import sqlite3
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "backup-db.sh"

# 判词自己认产物用的是**完整锚定**的正则，不是宽 glob。否则 `dawnop-manual.db.gz`
# 这种「长得像」的外来文件会被算进产物里，测试的账就跟着脚本一起错。
PRODUCT_RE = re.compile(r"^dawnop-\d{8}T\d{6}Z\.db\.gz$")


# --------------------------------------------------------------------------
# 跑脚本 / 造库 的小工具
# --------------------------------------------------------------------------


def run_backup(db: Path, dest: Path, keep: int = 14) -> subprocess.CompletedProcess:
    """把脚本当外部命令跑，路径全走环境变量（脚本可覆盖默认值就是为了这个）。"""
    env = {**os.environ, "DB": str(db), "DEST": str(dest), "KEEP": str(keep)}
    return subprocess.run(
        [str(SCRIPT)], env=env, capture_output=True, text=True, cwd=str(REPO)
    )


def make_db(path: Path, rows: int = 50) -> list[tuple[int, str]]:
    """造一个普通的小库，连接关掉（WAL 已归并回主文件）。"""
    con = sqlite3.connect(path)
    con.execute("create table article(id integer primary key, title text)")
    con.executemany(
        "insert into article(id, title) values(?, ?)",
        [(i, f"标题-{i:04d}") for i in range(rows)],
    )
    con.commit()
    con.close()
    return [(i, f"标题-{i:04d}") for i in range(rows)]


def products(dest: Path) -> list[str]:
    if not dest.is_dir():
        return []
    return sorted(p.name for p in dest.iterdir() if PRODUCT_RE.match(p.name))


def open_product(dest: Path, tmp_path: Path) -> sqlite3.Connection:
    """把唯一的产物解压成一个可查询的库。"""
    names = products(dest)
    assert len(names) == 1, f"期望恰好一个产物，实际 {names}"
    restored = tmp_path / "restored.db"
    with gzip.open(dest / names[0], "rb") as src, open(restored, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return sqlite3.connect(restored)


def corrupt_db_bytes(tmp_path: Path, want: str) -> bytes:
    """造一个「打得开、`.backup` 也退 0，但副本是坏的」的库，返回它的字节。

    `want` 说的是坏消息以什么形式出现，脚本对这两种各有一道判断：
      - `pragma-errors`  ：`sqlite3 副本 'PRAGMA integrity_check;'` 自己退非零
      - `pragma-reports` ：命令退 0，损坏只写在标准输出里

    具体哪一页、哪一段字节能造出哪一种，取决于 sqlite 的版本和页面布局，写死了
    换台机器就可能失效。所以这里是**试出来**的：逐个候选配方跑一遍脚本要跑的
    那两条命令，第一个满足前置条件的就用它，一个都没有就直接失败（不 skip，
    静默跳过的判词等于没有判词）。
    """
    src = tmp_path / "_corrupt_base.db"
    con = sqlite3.connect(src)
    con.execute("create table t(id integer primary key, v text)")
    con.execute("create index ix on t(v)")
    con.executemany(
        "insert into t(v) values(?)", [(f"value-{i:06d}",) for i in range(2000)]
    )
    con.commit()
    con.close()
    base = src.read_bytes()
    page_size = int.from_bytes(base[16:18], "big")

    probe = tmp_path / "_probe.db"
    snap = tmp_path / "_probe_snap.db"
    # 第一页含文件头，动了库就打不开了，从第二页起。
    for page in range(1, min(9, len(base) // page_size)):
        for lo, hi, fill in ((8, 200, 0xEE), (200, 600, 0x00), (600, 1200, 0xFF)):
            raw = bytearray(base)
            for i in range(lo, hi):
                raw[page * page_size + i] = fill
            probe.write_bytes(raw)
            snap.unlink(missing_ok=True)
            made = subprocess.run(
                ["sqlite3", str(probe), f".backup '{snap}'"],
                capture_output=True,
                text=True,
            )
            if made.returncode != 0:
                continue
            checked = subprocess.run(
                ["sqlite3", str(snap), "PRAGMA integrity_check;"],
                capture_output=True,
                text=True,
            )
            errored = checked.returncode != 0
            reported = checked.returncode == 0 and checked.stdout.strip() != "ok"
            if (want == "pragma-errors" and errored) or (
                want == "pragma-reports" and reported
            ):
                return bytes(raw)
    pytest.fail(f"没造出 {want} 这一类的损坏，判词的前提不成立了")


def seed_old_backups(dest: Path, count: int) -> list[str]:
    """预先摆 count 份「历史备份」，名字按时间递增。返回名字列表（旧→新）。"""
    dest.mkdir(parents=True, exist_ok=True)
    names = []
    for i in range(count):
        name = f"dawnop-20260101T{i:02d}0000Z.db.gz"
        (dest / name).write_bytes(b"old backup %d" % i)
        (dest / f"{name}.sha256").write_text(f"deadbeef  {name}\n")
        names.append(name)
    return names


# --------------------------------------------------------------------------
# 产物本身
# --------------------------------------------------------------------------


def test_product_restores_to_the_same_database(tmp_path):
    """解压得回一个 integrity_check 为 ok、且内容与源库逐行相同的库。

    这是整件事的目的：备份不是「有个文件」，是「能还原成同一个库」。
    """
    db = tmp_path / "src.db"
    expected = make_db(db)
    dest = tmp_path / "bk"

    proc = run_backup(db, dest)
    assert proc.returncode == 0, proc.stderr

    con = open_product(dest, tmp_path)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (
        con.execute("select id, title from article order by id").fetchall() == expected
    )
    con.close()

    names = products(dest)
    assert (dest / f"{names[0]}.sha256").exists()


def test_snapshot_is_consistent_while_a_writer_holds_an_open_transaction(tmp_path):
    """源库有活写者时，快照仍然自洽：已提交的行一条不少，未提交的一条不多。

    这条是选 `sqlite3 .backup` 而不是 `cp` 的全部理由，所以它必须被测到，而不是
    写在注释里。造场景的办法：WAL 模式 + 连接不关，于是**已提交**的数据还只在
    -wal 里、主库文件里没有；同时开着一个未提交的写事务。`cp` 主库文件在这里
    连表都拷不到，`.backup` 走在线备份 API 才拿得到一致的那一份。
    """
    db = tmp_path / "live.db"
    con = sqlite3.connect(db, isolation_level=None)
    con.execute("pragma journal_mode=wal")
    con.execute("create table t(v text)")
    con.executemany(
        "insert into t values(?)", [(f"committed-{i}",) for i in range(100)]
    )
    con.execute("begin immediate")
    con.executemany("insert into t values(?)", [(f"dirty-{i}",) for i in range(50)])

    dest = tmp_path / "bk"
    try:
        proc = run_backup(db, dest)
    finally:
        con.execute("rollback")
        con.close()
    assert proc.returncode == 0, proc.stderr

    snap = open_product(dest, tmp_path)
    assert snap.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert snap.execute("select count(*) from t").fetchone()[0] == 100
    dirty = snap.execute("select count(*) from t where v like 'dirty-%'").fetchone()[0]
    assert dirty == 0
    snap.close()


def test_sha256_sidecar_matches_the_product(tmp_path):
    """旁文件里的哈希得是产物**当下**的哈希，否则它只是一个装饰。"""
    db = tmp_path / "src.db"
    make_db(db)
    dest = tmp_path / "bk"
    assert run_backup(db, dest).returncode == 0

    name = products(dest)[0]
    digest = hashlib.sha256((dest / name).read_bytes()).hexdigest()
    line = (dest / f"{name}.sha256").read_text().strip()
    assert line.split()[0] == digest
    # sha256sum -c 要的是「名字」不是「路径」，否则换个目录就校验不了。
    assert line.split()[-1].lstrip("*") == name


def test_permissions_are_tight(tmp_path):
    """目录 700、产物与旁文件 600。

    备份里有管理员的 bcrypt 口令哈希和全部文章正文（含未发布的草稿），
    在多用户机器上它和库本身是同一级别的东西。
    """
    db = tmp_path / "src.db"
    make_db(db)
    dest = tmp_path / "bk"
    assert run_backup(db, dest).returncode == 0

    assert stat.S_IMODE(dest.stat().st_mode) == 0o700
    name = products(dest)[0]
    assert stat.S_IMODE((dest / name).stat().st_mode) == 0o600
    assert stat.S_IMODE((dest / f"{name}.sha256").stat().st_mode) == 0o600


# --------------------------------------------------------------------------
# 轮转
# --------------------------------------------------------------------------


def test_rotation_keeps_exactly_the_newest_keep(tmp_path):
    """摆 KEEP+3 份历史备份，跑完恰好剩 KEEP 份，且剩的是最新的那些。"""
    keep = 5
    db = tmp_path / "src.db"
    make_db(db)
    dest = tmp_path / "bk"
    old = seed_old_backups(dest, keep + 3)

    proc = run_backup(db, dest, keep=keep)
    assert proc.returncode == 0, proc.stderr

    remaining = products(dest)
    assert len(remaining) == keep
    # 新写的那一份（时间戳是今天）必然在，剩下的名额给最新的历史备份。
    fresh = [n for n in remaining if n not in old]
    assert len(fresh) == 1
    assert sorted(n for n in remaining if n in old) == old[-(keep - 1) :]
    # 被删的旧备份，旁文件也得跟着走，不能留一地孤儿 .sha256。
    for name in old[: -(keep - 1)]:
        assert not (dest / name).exists()
        assert not (dest / f"{name}.sha256").exists()


def test_rotation_does_not_touch_foreign_files(tmp_path):
    """轮转只删自己那个命名模式的文件，目录里别的东西一个都不许动。

    这是全脚本最危险的一段：它以 dawnop 身份 rm。判词摆四个「长得像但不是」的
    名字进去，宽 glob 一放松就会把它们卷走。
    """
    keep = 2
    db = tmp_path / "src.db"
    make_db(db)
    dest = tmp_path / "bk"
    seed_old_backups(dest, keep + 3)

    foreign = {
        "README.txt": b"how to restore",
        "dawnop-notes.txt": b"not a backup",
        "dawnop-20260101T000000Z.db.gz.bak": b"someone's hand copy",
        "backup.db.gz": b"a dump from another tool",
        "dawnop-manual.db.gz": b"timestamp-shaped? no.",
    }
    for name, body in foreign.items():
        (dest / name).write_bytes(body)

    proc = run_backup(db, dest, keep=keep)
    assert proc.returncode == 0, proc.stderr

    for name, body in foreign.items():
        assert (dest / name).exists(), f"轮转把外来文件 {name} 删了"
        assert (dest / name).read_bytes() == body
    assert len(products(dest)) == keep


# --------------------------------------------------------------------------
# 失败路径
# --------------------------------------------------------------------------


def test_missing_source_database_fails(tmp_path):
    """源库不存在 → 非零退出。

    不能靠 sqlite3 自己报错：`sqlite3 缺失的路径 .backup` 会**建一个空库**然后
    高高兴兴地备份它，于是每天一份 0 行的备份，直到需要它的那天。
    """
    dest = tmp_path / "bk"
    proc = run_backup(tmp_path / "nope.db", dest)
    assert proc.returncode != 0
    assert products(dest) == []


def test_corrupt_source_fails_and_leaves_no_debris(tmp_path):
    """源库是一堆垃圾字节 → 非零退出，且备份目录里一个文件都不剩。

    「不剩」包括临时文件：sqlite3 在这条路径上会留下一个 0 字节的目标文件，
    没人清的话它就一天攒一个。
    """
    db = tmp_path / "src.db"
    db.write_bytes(os.urandom(5000))
    dest = tmp_path / "bk"

    proc = run_backup(db, dest)
    assert proc.returncode != 0
    assert dest.is_dir()
    assert list(dest.iterdir()) == [], "失败路径留下了半成品"


def test_snapshot_that_cannot_be_integrity_checked_is_not_kept(tmp_path):
    """坏到 `PRAGMA integrity_check` 自己退非零 → 不留产物。

    `.backup` 是按页原样搬运，坏页会被忠实地搬进备份里，源库那一侧看不出问题
    （实测这些场景下 `.backup` 都退 0）。一份「能解压、打不开」的备份比没有备份
    更坏，因为它会让人以为自己有备份。
    """
    db = tmp_path / "src.db"
    db.write_bytes(corrupt_db_bytes(tmp_path, "pragma-errors"))

    dest = tmp_path / "bk"
    proc = run_backup(db, dest)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert products(dest) == [], "坏库的快照被当成正常备份留下了"


def test_snapshot_that_reports_corruption_is_not_kept(tmp_path):
    """`PRAGMA integrity_check` 退 0、但结果不是 `ok` → 不留产物。

    与上一条是两件事，不是同一件的两种写法：这类损坏下 sqlite3 命令本身**成功**，
    坏消息只在它的输出里。只看退出码的写法会把这份备份收下。
    """
    db = tmp_path / "src.db"
    db.write_bytes(corrupt_db_bytes(tmp_path, "pragma-reports"))

    dest = tmp_path / "bk"
    proc = run_backup(db, dest)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert products(dest) == [], "integrity_check 报了损坏，产物却留下了"


@pytest.mark.parametrize("keep", ["0", "-1", "abc"])
def test_bad_keep_is_rejected_before_anything_is_deleted(tmp_path, keep):
    """KEEP 讲不通时立刻退出，尤其 0：那不是备份，是删除。

    空串不在这里：`KEEP=` 走 `${KEEP:-14}` 回落到默认值，那是 shell 的常规语义，
    不是「讲不通的值」。
    """
    db = tmp_path / "src.db"
    make_db(db)
    dest = tmp_path / "bk"
    existing = seed_old_backups(dest, 3)

    proc = run_backup(db, dest, keep=keep)
    assert proc.returncode != 0
    assert products(dest) == existing
