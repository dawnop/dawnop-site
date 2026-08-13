"""库身份闩存：启动期核定一次并闩死，之后只读。

回滚到 FastAPI 时，最坏的失败不是「起不来」，而是「起来了，但连的是另一个库」——
`.env` 里的 `DATABASE_URL` 若还留着 M6 之前的相对路径默认值，站点会安静地退回一个旧库，
不报错，只是数据回到过去（deploy/README.md 里那条警告说的就是它）。所以回滚路径必须能
回答一个可核对的问题：**这个进程现在打开的，是不是运维手册里那个文件**。

指纹配方刻意选成一条运维在 shell 里能自己敲出来的等式：

    sha256("<st_dev>:<st_ino>")   ==   printf '%s' "$(stat -Lc '%d:%i' <path>)" | sha256sum

`tests/test_rollback_chain.py::test_fingerprint_matches_stat_and_sha256sum` 拿真文件把这条
等式钉住。钉住它的理由很实际：核对命令一旦和代码算的不是一个东西，手册里那行命令就是假的，
而它恰好只在生产着火的时候才会被第一次执行。

两条设计约束，都是被上一版实现的缺陷逼出来的：

1. **身份来自本进程自己 open 的 fd 上 fstat**，不去翻 `/proc/self/fd` 做快照差分。旧实现
   `dispose()` 连接池后对比前后 fd 集合，把新出现的 fd 归因给当前 Session——并发下别的东西
   也会开 fd，这个归因本身就是错的；而为了取一次身份就把连接池 dispose 掉，代价也荒谬。
2. **一次核定，之后闩死**。身份是进程级事实，不是每请求都要重算的东西。闩上之后若有人拿
   不同的指纹再来闩，说明「库在脚下被换了」，直接抛——宁可让守护进程非零退出，也不要带着
   一个说不清是哪个文件的库继续对外服务。
"""

import hashlib
import os
import stat
from urllib.parse import unquote


class DatabaseIdentityError(RuntimeError):
    """库身份核定失败：拿不到、对不上、或在已闩死后被要求改口。"""


def fingerprint_of_stat(st: os.stat_result) -> str:
    """指纹配方的唯一定义点。改这一行 = 改运维手册里的核对命令。"""
    return hashlib.sha256(f"{st.st_dev}:{st.st_ino}".encode()).hexdigest()


def database_path_from_url(database_url: str) -> str:
    """从 SQLAlchemy 的 sqlite URL 里取出文件路径（绝对化）。

    `sqlite:///./dawnop.db` → `<cwd>/dawnop.db`（相对，本地开发默认值）
    `sqlite:////opt/dawnop/data/dawnop.db` → `/opt/dawnop/data/dawnop.db`（四斜杠 = 绝对）
    """
    url = database_url.strip()
    scheme, sep, rest = url.partition("://")
    if not sep or scheme.split("+", 1)[0] != "sqlite":
        raise DatabaseIdentityError(f"只认识 sqlite URL，拿到的是 {database_url!r}")
    rest = rest.split("?", 1)[0]
    if not rest.startswith("/"):
        # sqlite URL 的 host 段必须是空的，路径从紧随其后的 / 开始
        raise DatabaseIdentityError(f"sqlite URL 缺少路径段: {database_url!r}")
    path = unquote(rest[1:])
    if not path or path.startswith(":memory:"):
        raise DatabaseIdentityError("内存库没有稳定身份，不能作为回滚目标")
    return os.path.abspath(path)


def fingerprint_of_path(path: str) -> str:
    """在**本进程亲自 open 的 fd** 上 fstat 取指纹。

    `O_CLOEXEC` 是为了这个 fd 绝不泄漏给任何 exec 出去的子进程；`finally` 里必闭。
    `os.open` 跟随符号链接，与核对命令的 `stat -L` 对齐。
    """
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise DatabaseIdentityError(f"打不开数据库文件 {path}: {exc}") from exc
    try:
        st = os.fstat(fd)
    finally:
        os.close(fd)
    if not stat.S_ISREG(st.st_mode):
        # 目录、fifo、/dev/null 之流都能 open 成功，但它们不是库。
        raise DatabaseIdentityError(f"{path} 不是普通文件，不能作为数据库")
    return fingerprint_of_stat(st)


class _DatabaseIdentityLatch:
    """一次性闩存：第一次 latch 记下，之后只允许原值复闩，读取要求已闩。"""

    def __init__(self) -> None:
        self._fingerprint: str | None = None

    @property
    def latched(self) -> bool:
        return self._fingerprint is not None

    def latch(self, fingerprint: str) -> str:
        if self._fingerprint is not None and self._fingerprint != fingerprint:
            raise DatabaseIdentityError(
                "库身份已闩死为 "
                f"{self._fingerprint}，现在被要求改成 {fingerprint}——"
                "库在运行期被换掉了"
            )
        self._fingerprint = fingerprint
        return fingerprint

    def read(self) -> str:
        if self._fingerprint is None:
            raise DatabaseIdentityError("库身份尚未核定")
        return self._fingerprint

    def reset(self) -> None:
        """仅供测试：把闩存打回未闩状态。生产路径没有任何调用点。"""
        self._fingerprint = None


_latch = _DatabaseIdentityLatch()


def _engine_main_database_file(database_url: str) -> str:
    """问引擎自己：你的 `main` 库落在哪个文件上。

    这不是多此一举——期望路径是我们从 URL 算的，而引擎是 SQLAlchemy 按同一个 URL 在
    某个 cwd 下真的打开的。相对路径 + 换过的工作目录、或者中途被换掉的符号链接，都会让
    这两个答案分叉，而只有这一步能看见分叉。
    """
    from app.database import engine

    with engine.connect() as conn:
        rows = conn.exec_driver_sql("PRAGMA database_list").fetchall()
    for row in rows:
        # (seq, name, file)
        if row[1] == "main":
            return str(row[2] or "")
    raise DatabaseIdentityError(f"PRAGMA database_list 没有 main 行: {rows!r}")


def establish_database_identity(
    database_url: str,
    expected_path: str | None = None,
    main_file_resolver=_engine_main_database_file,
) -> str:
    """启动期核定库身份并闩死；核不过就抛，让守护进程非零退出。

    顺序是**先由 url 定下期望指纹，再开库比对**，不能反过来：先开库再取期望，等于拿
    结果当期望，中途被换掉的文件就永远抓不出来了。
    """
    path = expected_path or database_path_from_url(database_url)
    expected = fingerprint_of_path(path)

    opened_file = main_file_resolver(database_url)
    if not opened_file:
        raise DatabaseIdentityError(
            f"引擎报告 main 库没有落在文件上（URL={database_url!r}）"
        )
    actual = fingerprint_of_path(opened_file)

    if actual != expected:
        raise DatabaseIdentityError(
            f"库身份对不上：URL 指向 {path}（{expected}），"
            f"引擎实际打开的是 {opened_file}（{actual}）"
        )
    return _latch.latch(expected)


def latched_database_fingerprint() -> str:
    """读闩死的指纹，供回滚探针用。未闩时抛。"""
    return _latch.read()


def database_identity_latched() -> bool:
    return _latch.latched


def reset_database_identity() -> None:
    """仅供测试。"""
    _latch.reset()
