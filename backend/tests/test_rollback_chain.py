"""回滚安全链：库身份闩存、守护式 lifespan、探针，以及两个部署脚本的结构性判词。

这条链是全仓最少被执行的代码——在生产着火之前没有任何东西会跑它，而那是最糟糕的
发现「说的和做的不一样」的时机。所以这里的测试分两类：

  * 能真跑的就真跑（指纹配方、闩存、探针状态码、bash 函数的行为）；
  * 跑不了的（要 root、要 systemd、要 nginx）改成对脚本文本的结构性断言——
    顺序、共享块的逐字节一致、常量与 systemd 单元对得上。

对应的负控在 backend/scripts/rollback_chain_mutants.py：每条断言都有一个能让它转红的
production mutant。「从没漏过东西」和「从没看过东西」输出一样，只有变异体能分。
"""

import hashlib
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest
from app.api import rollback as rollback_api
from app.config import settings
from app.core import db_identity, process_guard
from app.core.db_identity import (
    DatabaseIdentityError,
    _DatabaseIdentityLatch,
    establish_database_identity,
    fingerprint_of_path,
    fingerprint_of_stat,
)
from app.core.process_guard import ProcessFileRouteGuard
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]
ROLLBACK_SH = REPO / "backend-dawn" / "deploy" / "rollback-to-fastapi.sh"
RETURN_SH = REPO / "backend-dawn" / "deploy" / "return-to-dawn.sh"
FASTAPI_UNIT_FILE = REPO / "deploy" / "dawnop-backend.service"
DAWN_UNIT_FILE = REPO / "backend-dawn" / "deploy" / "dawnop-dawn.service"

BLOCK_START = "# >>> shared FastAPI verification block >>>"
BLOCK_END = "# <<< shared FastAPI verification block <<<"


# --------------------------------------------------------------------------
# 取文本的小工具
# --------------------------------------------------------------------------


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def shared_block(text: str) -> str:
    start = text.index(BLOCK_START)
    end = text.index(BLOCK_END) + len(BLOCK_END)
    return text[start:end]


def function_body(text: str, name: str) -> str:
    m = re.search(rf"^{re.escape(name)}\(\) \{{\n(.*?)^\}}$", text, re.M | re.S)
    assert m, f"{name}() 不在这段脚本里"
    return m.group(1)


def code_lines(text: str) -> list[tuple[int, str]]:
    """(原始行号, 行) —— 丢掉整行注释。

    只丢整行注释，不丢行内的 `#` 之后：这些脚本里的 sed 用 `#` 当分隔符
    （`s#proxy_pass...#...#`），按行内 `#` 截断会把真代码切掉一半。
    """
    return [
        (i, line)
        for i, line in enumerate(text.splitlines())
        if line.strip() and not line.lstrip().startswith("#")
    ]


def code_text(text: str) -> str:
    return "\n".join(line for _, line in code_lines(text))


def line_index(text: str, needle: str) -> int:
    for i, line in code_lines(text):
        if needle in line:
            return i
    raise AssertionError(f"脚本的代码行里找不到 {needle!r}")


def top_level_line_index(text: str, pattern: str) -> int:
    """顶格（第 0 列）的那一行——即脚本主干，不是某个函数体里的同名调用。

    这条区分是必要的：return-to-dawn.sh 的 `systemctl reload nginx` 出现两次，
    一次在 keep_fastapi_and_bail() 的还原路径里（缩进的，且在文件靠前），一次在
    主干第 3 步。按「第一次出现」定位会锚到函数定义上，于是「reload 之后有没有复验」
    这条判词会被第 2 步的调用蒙混过关——恒真。
    """
    for i, line in code_lines(text):
        if re.match(pattern, line):
            return i
    raise AssertionError(f"脚本里没有顶格的 {pattern!r}")


def python_code_only(path: Path) -> str:
    """把 .py 的注释与字符串（含文档字符串）剔掉，只留代码。

    否则「源码里不许出现 X」这类判词会被解释 X 为什么不该出现的那段注释自己绊倒。
    """
    import io
    import tokenize

    kept = []
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(io.BytesIO(fh.read()).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(tok.string)
    return " ".join(kept)


def readme_section(title: str) -> str:
    """deploy/README.md 里 `## <title>` 到下一个 `## ` 之间的那一段。

    整篇 README 里找一个词太松：`ProtectSystem` 在「二、3」也出现，拿它当「回滚一节说了
    加固不对称」的证据就是恒真的。判词要落在那一节里。
    """
    readme = read(REPO / "deploy" / "README.md")
    start = readme.index(f"## {title}")
    rest = readme[start + 1 :]
    m = re.search(r"^## ", rest, re.M)
    return rest[: m.start()] if m else rest


# systemd 的沙箱类指令。不求穷尽，求覆盖两个单元今天用到的与最可能被补上的那些。
HARDENING_DIRECTIVES = frozenset(
    {
        "NoNewPrivileges",
        "ProtectSystem",
        "ProtectHome",
        "PrivateTmp",
        "PrivateDevices",
        "ProtectKernelTunables",
        "ProtectKernelModules",
        "ProtectControlGroups",
        "RestrictAddressFamilies",
        "RestrictNamespaces",
        "RestrictSUIDSGID",
        "LockPersonality",
        "MemoryDenyWriteExecute",
        "SystemCallFilter",
        "CapabilityBoundingSet",
    }
)


def hardening_directives(path: Path) -> set[str]:
    return HARDENING_DIRECTIVES & set(unit_directives(path))


def unit_directives(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in read(path).splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "[")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out.setdefault(key.strip(), value.strip())
    return out


@pytest.fixture
def block_file(tmp_path):
    """把共享块单独落成一个可 source 的文件，好在测试里真调它的 bash 函数。"""
    path = tmp_path / "block.sh"
    path.write_text(shared_block(read(ROLLBACK_SH)) + "\n", encoding="utf-8")
    return path


def run_block(block_file: Path, snippet: str, stdin: bytes = b"") -> str:
    return run_block_full(block_file, snippet, stdin)[0]


def run_block_full(
    block_file: Path, snippet: str, stdin: bytes = b""
) -> tuple[str, str]:
    """(stdout, stderr)。逃生阀的回显走 stderr——它不能污染 stdout 上的判定结果。"""
    proc = subprocess.run(
        ["bash", "-c", f". {shlex.quote(str(block_file))}\n{snippet}"],
        input=stdin,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    return proc.stdout.decode(), proc.stderr.decode()


# --------------------------------------------------------------------------
# 1. 库身份：指纹配方
# --------------------------------------------------------------------------


def test_fingerprint_matches_stat_and_sha256sum(tmp_path, block_file):
    """Python 算的指纹 == 脚本里 expected_database_fingerprint 算的指纹。

    这条是运维手册那行核对命令的真伪。配方一旦漂开，手册里的命令就是假的，而它只在
    生产着火的时候才会被第一次执行。所以比的不是「一条我在测试里现写的 shell 管道」，
    而是**脚本里真正那个函数**——从共享块里 source 出来直接调。
    """
    target = tmp_path / "dawnop.db"
    target.write_bytes(b"not really a database")

    from_shell = run_block(
        block_file,
        f"DAWNOP_DB_FILE={shlex.quote(str(target))}\nexpected_database_fingerprint\n",
    ).strip()

    assert fingerprint_of_path(str(target)) == from_shell


def test_deploy_readme_verification_command_actually_works(tmp_path):
    """运维手册里那行核对命令**照抄下来跑**，结果必须等于进程会闩住的指纹。

    这一节只在生产着火时被第一次执行。一条抄错了的命令在那一刻的表现是「库身份对不上」，
    而人会去查库，不会去怀疑手册。所以这里把 README 的那一行原样抠出来跑一遍。
    """
    readme = read(REPO / "deploy" / "README.md")
    m = re.search(
        r"^printf '%s' \"\$\(stat -Lc '%d:%i' -- (\S+)\)\" \| sha256sum$", readme, re.M
    )
    assert m, "deploy/README.md 里那条核对命令的形状变了"

    target = tmp_path / "dawnop.db"
    target.write_bytes(b"x")
    command = m.group(0).replace(m.group(1), shlex.quote(str(target)))
    out = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True, check=True
    )

    assert out.stdout.split()[0] == fingerprint_of_path(str(target))


def test_deploy_readme_documents_the_probe_header_name():
    readme = read(REPO / "deploy" / "README.md")
    assert rollback_api.PROBE_HEADER_NAME in readme
    assert "/api/rollback/db-identity" in readme


def test_fingerprint_recipe_is_dev_colon_ino(tmp_path):
    target = tmp_path / "db"
    target.write_bytes(b"x")
    st = os.stat(target)
    expected = hashlib.sha256(f"{st.st_dev}:{st.st_ino}".encode()).hexdigest()
    assert fingerprint_of_stat(st) == expected
    assert fingerprint_of_path(str(target)) == expected


def test_fingerprint_follows_symlinks_like_stat_dash_l(tmp_path):
    target = tmp_path / "real.db"
    target.write_bytes(b"x")
    link = tmp_path / "link.db"
    link.symlink_to(target)
    assert fingerprint_of_path(str(link)) == fingerprint_of_path(str(target))


def test_fingerprint_opens_its_own_fd_and_closes_it(tmp_path, monkeypatch):
    """身份来自本进程自己 open 的 fd 上 fstat，不去翻 /proc/self/fd 归因。"""
    target = tmp_path / "db"
    target.write_bytes(b"x")

    seen: list[int] = []
    opened: list[int] = []
    closed: list[int] = []
    real_open, real_close = os.open, os.close

    def spy_open(path, flags, *a, **kw):
        seen.append(flags)
        fd = real_open(path, flags, *a, **kw)
        opened.append(fd)
        return fd

    def spy_close(fd):
        closed.append(fd)
        return real_close(fd)

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "close", spy_close)
    fingerprint_of_path(str(target))

    assert seen, "根本没有 open"
    assert seen[0] & os.O_CLOEXEC, "fd 没带 O_CLOEXEC，会泄漏给 exec 出去的子进程"
    assert opened and opened[0] in closed, "open 出来的 fd 没被关掉"
    assert "/proc/self/fd" not in python_code_only(Path(db_identity.__file__))


def test_fingerprint_rejects_non_regular_file(tmp_path):
    with pytest.raises(DatabaseIdentityError):
        fingerprint_of_path(str(tmp_path))


def test_fingerprint_rejects_missing_file(tmp_path):
    with pytest.raises(DatabaseIdentityError):
        fingerprint_of_path(str(tmp_path / "nope.db"))


def test_dead_code_from_the_old_per_request_scheme_is_gone():
    """旧实现每请求 dispose 连接池 + 翻 /proc/self/fd 做差分归因，随之而来的三个名字必须没了。"""
    source = python_code_only(Path(db_identity.__file__))
    for gone in (
        "_open_file_descriptors",
        "_opened_main_database_identity",
        "database_fingerprint",
    ):
        # 词边界：latched_database_fingerprint 是留下来的那个，不该被这条误伤。
        assert not re.search(rf"\b{gone}\b", source), f"{gone} 还在"


# --------------------------------------------------------------------------
# 1. 库身份：闩存
# --------------------------------------------------------------------------


def test_latch_read_before_latch_raises():
    latch = _DatabaseIdentityLatch()
    assert latch.latched is False
    with pytest.raises(DatabaseIdentityError):
        latch.read()


def test_latch_accepts_the_same_fingerprint_twice():
    latch = _DatabaseIdentityLatch()
    latch.latch("aa")
    latch.latch("aa")
    assert latch.read() == "aa"


def test_latch_rejects_second_different_fingerprint():
    latch = _DatabaseIdentityLatch()
    latch.latch("aa")
    with pytest.raises(DatabaseIdentityError):
        latch.latch("bb")
    assert latch.read() == "aa", "被拒之后旧值也不许被改写"


def test_establish_latches_and_is_readable(tmp_path):
    db = tmp_path / "dawnop.db"
    db.write_bytes(b"x")
    db_identity.reset_database_identity()
    try:
        got = establish_database_identity(
            f"sqlite:///{db}", main_file_resolver=lambda _url: str(db)
        )
        assert got == fingerprint_of_path(str(db))
        assert db_identity.database_identity_latched() is True
        assert db_identity.latched_database_fingerprint() == got
    finally:
        db_identity.reset_database_identity()


def test_establish_rejects_a_different_opened_file(tmp_path):
    db = tmp_path / "dawnop.db"
    db.write_bytes(b"x")
    other = tmp_path / "old.db"
    other.write_bytes(b"y")
    db_identity.reset_database_identity()
    try:
        with pytest.raises(DatabaseIdentityError):
            establish_database_identity(
                f"sqlite:///{db}", main_file_resolver=lambda _url: str(other)
            )
        assert db_identity.database_identity_latched() is False
    finally:
        db_identity.reset_database_identity()


def test_establish_takes_the_expected_fingerprint_before_opening(tmp_path):
    """顺序判词：**先**由 url 定下期望指纹，**再**开库比对。

    resolver 在被调用的那一刻把路径下的文件换掉（新 inode），然后原样返回那个路径。
    先取期望 → 两个指纹不同 → 抛。反过来先开库再取期望，等于拿结果当期望，
    这个换文件的动作就永远抓不出来。
    """
    db = tmp_path / "dawnop.db"
    db.write_bytes(b"original")

    def swap_then_report(_url):
        replacement = tmp_path / "replacement.db"
        replacement.write_bytes(b"impostor")
        os.replace(replacement, db)
        return str(db)

    db_identity.reset_database_identity()
    try:
        with pytest.raises(DatabaseIdentityError, match="库身份对不上"):
            establish_database_identity(
                f"sqlite:///{db}", main_file_resolver=swap_then_report
            )
    finally:
        db_identity.reset_database_identity()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("sqlite:////opt/dawnop/data/dawnop.db", "/opt/dawnop/data/dawnop.db"),
        ("sqlite+pysqlite:////opt/dawnop/data/dawnop.db", "/opt/dawnop/data/dawnop.db"),
    ],
)
def test_database_path_from_url_absolute(url, expected):
    assert db_identity.database_path_from_url(url) == expected


def test_database_path_from_url_relative_is_absolutised(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    got = db_identity.database_path_from_url("sqlite:///./dawnop.db")
    assert got == str(tmp_path / "dawnop.db")


@pytest.mark.parametrize(
    "url", ["postgresql://x/y", "sqlite://", "sqlite:///:memory:", "nonsense"]
)
def test_database_path_from_url_rejects_what_has_no_identity(url):
    with pytest.raises(DatabaseIdentityError):
        db_identity.database_path_from_url(url)


# --------------------------------------------------------------------------
# 2. 进程守卫与守护式 lifespan
# --------------------------------------------------------------------------


def test_guard_reads_the_sentinel_once_and_never_again(tmp_path):
    sentinel = tmp_path / "fastapi-file-routes-disabled"
    sentinel.write_text("")
    guard = ProcessFileRouteGuard(str(sentinel))
    assert guard.latched is False
    assert guard.file_routes_disabled() is True
    assert guard.latched is True

    sentinel.unlink()
    assert guard.file_routes_disabled() is True, "闩上之后不许再回头看磁盘"


def test_guard_absent_sentinel_also_latches(tmp_path):
    sentinel = tmp_path / "absent"
    guard = ProcessFileRouteGuard(str(sentinel))
    assert guard.file_routes_disabled() is False
    sentinel.write_text("")
    assert guard.file_routes_disabled() is False, "闩上之后不许再回头看磁盘"


def test_guard_sentinel_path_matches_the_scripts():
    assert f'FILE_ROUTE_SENTINEL="{process_guard.SENTINEL_PATH}"' in shared_block(
        read(ROLLBACK_SH)
    )


def test_lifespan_guarded_establishes_identity_and_leaves_the_schema_alone(monkeypatch):
    from app import main

    calls: list[str] = []
    monkeypatch.setattr(main, "process_file_routes_disabled", lambda: True)
    monkeypatch.setattr(
        main, "establish_database_identity", lambda url: calls.append(f"identity:{url}")
    )
    monkeypatch.setattr(main, "init_db", lambda: calls.append("init_db"))
    monkeypatch.setattr(
        main, "ensure_builtin_pages", lambda: calls.append("ensure_builtin_pages")
    )
    monkeypatch.setattr(
        main, "ensure_article_fts", lambda _e: calls.append("ensure_article_fts")
    )

    with TestClient(main.app):
        pass

    assert calls == [f"identity:{settings.database_url}"]


def test_lifespan_unguarded_initialises_the_schema(monkeypatch):
    from app import main

    calls: list[str] = []
    monkeypatch.setattr(main, "process_file_routes_disabled", lambda: False)
    monkeypatch.setattr(
        main, "establish_database_identity", lambda url: calls.append("identity")
    )
    monkeypatch.setattr(main, "init_db", lambda: calls.append("init_db"))
    monkeypatch.setattr(
        main, "ensure_builtin_pages", lambda: calls.append("ensure_builtin_pages")
    )
    monkeypatch.setattr(
        main, "ensure_article_fts", lambda _e: calls.append("ensure_article_fts")
    )

    with TestClient(main.app):
        pass

    assert calls == ["init_db", "ensure_builtin_pages", "ensure_article_fts"]
    assert "identity" not in calls


def test_guarded_process_refuses_file_routes(client, monkeypatch, auth_headers):
    from app import main

    monkeypatch.setattr(main, "process_file_routes_disabled", lambda: True)
    resp = client.get("/api/fm", params={"path": "qiniu://"}, headers=auth_headers)
    assert resp.status_code == 503


def test_guarded_file_route_refusal_outranks_authentication(client, monkeypatch):
    """没带 token 也要先撞 503：守卫是路由级的，不该由「先查鉴权」把它遮住。

    脚本里 `local_status_is ... /api/fm 503` 就是靠这条成立的——它不带 Authorization。
    """
    from app import main

    monkeypatch.setattr(main, "process_file_routes_disabled", lambda: True)
    assert client.get("/api/fm", params={"path": "qiniu://"}).status_code == 503


def test_unguarded_process_serves_file_routes(client, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "process_file_routes_disabled", lambda: False)
    assert client.get("/api/fm", params={"path": "qiniu://"}).status_code == 401


# --------------------------------------------------------------------------
# 2. 探针
# --------------------------------------------------------------------------


@pytest.fixture
def probe_secret(monkeypatch):
    monkeypatch.setattr(rollback_api.settings, "rollback_probe_header", "probe-secret")
    return {"X-Rollback-Probe": "probe-secret"}


@pytest.fixture
def latched(monkeypatch):
    db_identity.reset_database_identity()
    db_identity._latch.latch("deadbeef")
    yield "deadbeef"
    db_identity.reset_database_identity()


def test_probe_404_without_the_header(client, probe_secret, latched):
    resp = client.get("/api/rollback/db-identity")
    assert resp.status_code == 404, "403 等于确认这个端点存在"


def test_probe_404_with_a_wrong_header(client, probe_secret, latched):
    resp = client.get(
        "/api/rollback/db-identity", headers={"X-Rollback-Probe": "guess"}
    )
    assert resp.status_code == 404


def test_probe_404_when_no_secret_is_configured(client, monkeypatch, latched):
    monkeypatch.setattr(rollback_api.settings, "rollback_probe_header", "")
    resp = client.get("/api/rollback/db-identity", headers={"X-Rollback-Probe": ""})
    assert resp.status_code == 404, "空秘密绝不能被当成「谁都能进」"


def test_probe_503_when_identity_is_not_latched(client, probe_secret):
    db_identity.reset_database_identity()
    resp = client.get("/api/rollback/db-identity", headers=probe_secret)
    assert resp.status_code == 503
    assert "database_fingerprint" not in resp.text, "未闩时不许回指纹——那是编的"


def test_probe_returns_the_latched_fingerprint(client, probe_secret, latched):
    resp = client.get("/api/rollback/db-identity", headers=probe_secret)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database_fingerprint"] == latched
    assert body["process_guard_latched"] in (True, False)


def test_probe_is_not_in_the_openapi_schema(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if "rollback" in p]


# --------------------------------------------------------------------------
# 3. 没有 FASTAPI_VERIFIED 这类缓存结论
# --------------------------------------------------------------------------


def test_no_cached_fastapi_verification():
    """任何「保留 FastAPI」或「恢复 FastAPI」的路径都必须重新完整验一遍。

    旧实现有个 FASTAPI_VERIFIED，让一次核验的结论跨命令复用。陈旧的结论和没有结论
    长得一样，而回滚链上每一次「保留 FastAPI」的决定都发生在世界刚刚变过之后。
    """
    for path in (ROLLBACK_SH, RETURN_SH):
        code = code_text(read(path))
        assert "FASTAPI_VERIFIED" not in code, f"{path.name} 又出现了缓存结论"
        assert not re.search(r"^\s*[A-Z_]*VERIFIED[A-Z_]*=", code, re.M), (
            f"{path.name} 里有 *VERIFIED* 赋值，八成又是缓存"
        )


def test_keeping_fastapi_reruns_the_full_verification():
    body = function_body(read(RETURN_SH), "keep_fastapi_and_bail")
    assert "verify_guarded_fastapi_runtime" in body, (
        "「保留 FastAPI」这条路径没有重新完整核验"
    )


# --------------------------------------------------------------------------
# 4. 回切之后要重新核验
# --------------------------------------------------------------------------


def test_return_to_dawn_reverifies_after_reload():
    text = read(RETURN_SH)
    reload_at = top_level_line_index(text, r"systemctl reload nginx\s*$")
    after = [
        i
        for i, line in code_lines(text)
        if i > reload_at and re.match(r"verify_dawn_runtime\b", line)
    ]
    assert after, "reload 之后没有任何一次重新核验——切回去了不等于健康"


def test_return_to_dawn_probes_public_surface_after_reload():
    text = read(RETURN_SH)
    reload_at = top_level_line_index(text, r"systemctl reload nginx\s*$")
    assert [
        i
        for i, line in code_lines(text)
        if i > reload_at and "probe_suite https" in line
    ], "reload 之后没有公网探活"


# --------------------------------------------------------------------------
# 5. Dawn 侧完整探活
# --------------------------------------------------------------------------


def dawn_probe_specs() -> list[str]:
    text = read(RETURN_SH)
    m = re.search(r"^DAWN_PROBES=\(\n(.*?)^\)$", text, re.M | re.S)
    assert m, "return-to-dawn.sh 里没有 DAWN_PROBES"
    return [line.strip().strip('"') for line in m.group(1).splitlines() if line.strip()]


def test_dawn_liveness_is_more_than_one_health_call():
    specs = dawn_probe_specs()
    paths = {spec.split()[1].split("?")[0] for spec in specs}
    assert "/api/health" in paths
    # /api/health 不碰库、不碰七牛、不碰鉴权，它绿着的时候后端可以是任何程度的坏。
    assert len(paths - {"/api/health"}) >= 4, f"探活面太窄：{sorted(paths)}"
    for required in ("/api/articles", "/api/pages/nav", "/api/search", "/api/settings"):
        assert required in paths, f"完整探活缺 {required}"


def test_dawn_liveness_asserts_authentication_is_wired():
    specs = {
        spec.split()[1].split("?")[0]: spec.split()[2] for spec in dawn_probe_specs()
    }
    assert specs["/api/settings"] == "401", "没 token 却不是 401 = 鉴权没接上"


def test_dawn_liveness_distinguishes_itself_from_the_guarded_fastapi():
    """/api/fm 期望 401：受守护的 FastAPI 在同一路径上回 503。

    这是两个后端之间唯一一个不看端口就能分辨的判词——没有它，「回切成功」可能只是
    「nginx reload 成功了，流量其实还在 FastAPI 上」。
    """
    specs = {
        spec.split()[1].split("?")[0]: spec.split()[2] for spec in dawn_probe_specs()
    }
    assert specs["/api/fm"] == "401"
    assert "GET /api/fm?path=qiniu:// 401" in read(RETURN_SH)


# --------------------------------------------------------------------------
# 6. 两个方向的单元/argv/cwd/UID/GID 必须对齐
# --------------------------------------------------------------------------


def test_shared_fastapi_verification_block_is_identical():
    """两个脚本里的共享块**逐字节**相同。差一个空格就红。"""
    a = shared_block(read(ROLLBACK_SH))
    b = shared_block(read(RETURN_SH))
    assert a.encode() == b.encode(), (
        "共享 FastAPI 核验块漂了：回滚与回切验的不再是同一个 FastAPI。"
        "改动要同时落到两个脚本上。"
    )
    assert a.count("\n") > 100, "共享块被掏空了，比对器还在但没东西可比"


def test_fastapi_identity_constants_match_the_systemd_unit():
    block = shared_block(read(ROLLBACK_SH))
    unit = unit_directives(FASTAPI_UNIT_FILE)
    assert f'FASTAPI_WORKDIR="{unit["WorkingDirectory"]}"' in block
    assert f'FASTAPI_USER="{unit["User"]}"' in block
    assert f'FASTAPI_GROUP="{unit["Group"]}"' in block
    # ARGV **不**在这里比。它不等于 ExecStart=，理由见下面两条。


def test_dawn_identity_constants_match_the_systemd_unit():
    text = read(RETURN_SH)
    unit = unit_directives(DAWN_UNIT_FILE)
    assert f'DAWN_WORKDIR="{unit["WorkingDirectory"]}"' in text
    assert f'DAWN_USER="{unit["User"]}"' in text
    assert f'DAWN_GROUP="{unit["Group"]}"' in text


ARGV_CASES = [
    ("FASTAPI", ROLLBACK_SH, FASTAPI_UNIT_FILE),
    ("DAWN", RETURN_SH, DAWN_UNIT_FILE),
]


def shell_constant(text: str, name: str) -> str:
    m = re.search(rf'^{name}="(.*)"$', text, re.M)
    assert m, f"找不到常量 {name}"
    return m.group(1)


@pytest.mark.parametrize(("label", "script", "unit_file"), ARGV_CASES)
def test_argv_constant_starts_with_the_pinned_interpreter(label, script, unit_file):
    """argv[0] 是**内核实际执行的那个程序**，不是 systemd 的 ExecStart=。

    ExecStart= 指向 shebang 脚本时（uvicorn 就是一个 `#!.../python3` 的 console script），
    内核 exec 会把解释器插到 argv[0]，于是 `/proc/PID/cmdline` 恒比 ExecStart= 多一个 token。
    照 ExecStart= 抄的常量因此在**任何**机器上都匹配不上。这条判词此前不存在，取而代之的是
    「ARGV 必须等于 ExecStart」，那条恰好把错的东西钉死了：2026-08-14 的生产演练里，
    回滚停在 verify_unit_identity 的 argv 那一步，而在那之前它从没对着一个真进程跑过。
    """
    text = read(script)
    argv = shell_constant(text, f"{label}_ARGV")
    pinned = shell_constant(text, f"{label}_PINNED_INTERPRETER")
    assert argv.split(" ")[0] == pinned, (
        f"{label}_ARGV 的 argv[0] 是 {argv.split(' ')[0]!r}，"
        f"而钉住的解释器是 {pinned!r}；/proc/PID/cmdline 里 argv[0] 一定是后者"
    )


@pytest.mark.parametrize(("label", "script", "unit_file"), ARGV_CASES)
def test_argv_constant_is_execstart_modulo_the_interpreter_prefix(
    label, script, unit_file
):
    """常量仍必须由 ExecStart= 派生，只是允许前面多一个解释器。

    只要求「以钉住的解释器开头」是不够的：那样 ARGV 的其余部分可以和单元完全无关，
    而单元才是真正决定进程被怎么拉起来的东西。
    """
    text = read(script)
    argv = shell_constant(text, f"{label}_ARGV")
    pinned = shell_constant(text, f"{label}_PINNED_INTERPRETER")
    execstart = unit_directives(unit_file)["ExecStart"]
    assert argv in (execstart, f"{pinned} {execstart}"), (
        f"{label}_ARGV 既不等于 ExecStart=，也不等于「解释器 + ExecStart=」：\n"
        f"  ARGV      = {argv!r}\n  ExecStart = {execstart!r}"
    )


def test_fastapi_constants_live_only_in_the_shared_block():
    """FastAPI 侧的常量不许在块外再定义一份——那正是两边漂开的起点。"""
    for path in (ROLLBACK_SH, RETURN_SH):
        text = read(path)
        outside = text.replace(shared_block(text), "")
        assert not re.search(r"^FASTAPI_[A-Z_]+=", outside, re.M), path.name


def test_both_directions_verify_the_same_way():
    """两个脚本都要**调用**共享的核验驱动，不是「块里有这个函数的定义」就算数。

    定义在共享块里，两边永远都有——只看名字在不在，这条判词就是恒真的。
    """
    for path in (ROLLBACK_SH, RETURN_SH):
        text = read(path)
        outside = code_text(text.replace(shared_block(text), ""))
        assert re.search(
            r"^\s*verify_guarded_fastapi_runtime\s*(;|$)", outside, re.M
        ), f"{path.name} 从没调用过 verify_guarded_fastapi_runtime"


def test_hardening_comment_matches_what_the_two_units_actually_carry():
    """Dawn 单元那条加固注释说的，必须是两个单元的实测状态。

    它一度写着「mirrors the FastAPI unit」。实测不是：Dawn 单元有四条沙箱指令，
    `deploy/dawnop-backend.service` 一条都没有。回滚等于切到一个未加固的进程上，而注释
    宣称两者一样。这不是排版问题，是一条会让人在事故中做出错误风险判断的假陈述。

    这条断言钉的是「注释 == 实测」，不是某个字符串。日后给 FastAPI 单元补上加固、或者把
    Dawn 单元的加固删掉，它都会转红，那时该改的是注释（以及这条断言），不是绕开它。
    """
    dawn = hardening_directives(DAWN_UNIT_FILE)
    fastapi = hardening_directives(FASTAPI_UNIT_FILE)
    assert dawn, "Dawn 单元不再有任何加固指令，注释与 README 的不对称论述要重写"
    assert not fastapi, (
        f"FastAPI 单元现在带了加固指令 {sorted(fastapi)}：两个单元不再不对称，"
        "dawnop-dawn.service 的注释与 deploy/README.md「四」的警告都要重写"
    )

    text = read(DAWN_UNIT_FILE)
    assert "mirrors the FastAPI unit" not in text, (
        "注释又在宣称与 FastAPI 单元一致，而实测两者不对称"
    )
    assert FASTAPI_UNIT_FILE.name in text, "注释没点名不对称的另一方，读的人无从核对"
    assert "#249" in text, "注释没留下「为什么当初没补」的记号（回滚演练那一件）"


def test_deploy_readme_warns_that_the_rollback_target_is_unhardened():
    """做回滚决策的人要知道自己在接受什么：回滚同时把 Dawn 单元那几层约束一起摘掉。

    注释只有改这个单元的人会看见；按下回滚的人看的是 README 那一节。
    """
    section = readme_section("四、回滚安全链")
    assert "未加固" in section, "回滚一节没说清回滚落到的是一个未加固的进程"
    for directive in sorted(hardening_directives(DAWN_UNIT_FILE)):
        assert directive in section, f"回滚一节没点名 FastAPI 单元缺的 {directive}"


def test_unit_identity_check_covers_unit_argv_cwd_uid_gid():
    body = function_body(shared_block(read(ROLLBACK_SH)), "verify_unit_identity")
    assert "/cgroup" in body, "没确认 PID 真属于这个单元"
    assert "/cmdline" in body, "没比 argv"
    assert "/cwd" in body, "没比工作目录"
    # 第 3 列 = 有效 UID/GID。
    assert "'/^Uid:/ { print $3 }'" in body
    assert "'/^Gid:/ { print $3 }'" in body


def test_dawn_runtime_verification_reuses_the_shared_predicates():
    body = function_body(read(RETURN_SH), "verify_dawn_runtime")
    for fn in (
        "verify_unit_identity",
        "verify_pinned_interpreter",
        "verify_unit_loader_environment",
        "verify_process_loader_environment",
    ):
        assert fn in body, f"Dawn 侧少了 {fn}——两个方向验的不是同一组东西"


# --------------------------------------------------------------------------
# 7. /proc/$PID/exe 与加载器漂移
# --------------------------------------------------------------------------


def test_pinned_interpreter_is_compared_through_proc_exe():
    """实测值必须**取自** /proc/$PID/exe，而不是只在报错信息里提一句它。"""
    block = shared_block(read(ROLLBACK_SH))
    body = function_body(block, "verify_pinned_interpreter")
    assert re.search(
        r'^\s*actual=\$\(resolve_path "/proc/\$pid/exe"\)\s*$', body, re.M
    ), "actual 不是从 /proc/$pid/exe 取的"
    assert re.search(
        r'^\s*pinned=\$\(resolve_path "\$pinned_path"\)\s*$', body, re.M
    ), "钉住的解释器没有解析后再比"
    assert '[ "$actual" != "$pinned" ]' in body, "取到了却没比"
    assert "readlink -f" in function_body(block, "resolve_path")


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (b"PATH=/usr/bin\0HOME=/root\0", ""),
        (b"PATH=/usr/bin\0PYTHONPATH=/tmp/evil\0", "PYTHONPATH"),
        (b"PYTHONHOME=/tmp/evil\0", "PYTHONHOME"),
        (b"LD_PRELOAD=/tmp/evil.so\0", "LD_PRELOAD"),
        (b"LD_LIBRARY_PATH=/tmp\0", "LD_LIBRARY_PATH"),
        (b"PATH=/usr/bin LD_PRELOAD=/tmp/evil.so", "LD_PRELOAD"),
    ],
)
def test_loader_environment_offender(block_file, env, expected):
    """NUL 分隔（/proc/PID/environ）与空格分隔（systemctl show）两种输入都要认。"""
    assert (
        run_block(block_file, "loader_environment_offender\n", env).strip() == expected
    )


ESCAPE_SNIPPET = "ROLLBACK_ALLOW_ENV={}\nloader_environment_offender\n"


@pytest.mark.parametrize(
    ("allow", "env", "expected"),
    [
        # 放行的必须是**完整变量名**。前缀、子串、glob 一律不算数。
        ("PYTHONPATH", b"PYTHONPATH=/tmp/evil\0", ""),
        ("PYTHON", b"PYTHONPATH=/tmp/evil\0", "PYTHONPATH"),
        ("PYTHONPATH_EXTRA", b"PYTHONPATH=/tmp/evil\0", "PYTHONPATH"),
        ("PATH", b"PYTHONPATH=/tmp/evil\0", "PYTHONPATH"),
        ("PYTHON*", b"PYTHONPATH=/tmp/evil\0", "PYTHONPATH"),
        ("PYTHONUNBUFFERED", b"PYTHONUNBUFFERED_EXTRA=1\0", "PYTHONUNBUFFERED_EXTRA"),
        ("LD_", b"LD_PRELOAD=/tmp/evil.so\0", "LD_PRELOAD"),
        # 多个名字：空格分隔，全列上才全放行。
        (
            "PYTHONUNBUFFERED LD_PRELOAD",
            b"PYTHONUNBUFFERED=1\0LD_PRELOAD=/tmp/evil.so\0",
            "",
        ),
        # ★ 「第一个 offender 恰好被放行」不等于整条流干净：报的必须是第一个**没被放行的**。
        (
            "PYTHONUNBUFFERED",
            b"PYTHONUNBUFFERED=1\0PYTHONPATH=/tmp/evil\0",
            "PYTHONPATH",
        ),
        (
            "PYTHONDONTWRITEBYTECODE PYTHONUNBUFFERED",
            b"PYTHONDONTWRITEBYTECODE=1\0PYTHONUNBUFFERED=1\0LD_PRELOAD=/tmp/evil.so\0",
            "LD_PRELOAD",
        ),
    ],
)
def test_allow_env_releases_only_the_exact_variable_name(
    block_file, allow, env, expected
):
    got = run_block(block_file, ESCAPE_SNIPPET.format(shlex.quote(allow)), env)
    assert got.strip() == expected


@pytest.mark.parametrize(
    "name", ["PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE", "LD_LIBRARY_PATH"]
)
def test_allow_env_echoes_the_released_variable_name(block_file, name):
    """放行必须留痕，且留的是**那个名字**。

    「已放行一个变量」这种不点名的日志等于无声接受，而无声接受正是这个逃生阀存在
    要消除的东西：它把「被挡住」换成「有意识地接受」，代价必须是日志里那一行。
    参数化过三个名字，是为了让「把名字硬编码进消息」这种假回显也红。
    """
    env = name.encode() + b"=1\0"
    stdout, stderr = run_block_full(
        block_file, ESCAPE_SNIPPET.format(shlex.quote(name)), env
    )
    assert stdout.strip() == "", "被放行的变量不该再出现在判定结果里"
    assert name in stderr, f"日志里没有回显被放行的 {name}"
    assert "ROLLBACK_ALLOW_ENV" in stderr, "没说清是逃生阀放的行"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (b"PATH=/usr/bin\0HOME=/root\0", ""),
        (b"PATH=/usr/bin\0PYTHONPATH=/tmp/evil\0", "PYTHONPATH"),
        (b"PYTHONUNBUFFERED=1\0", "PYTHONUNBUFFERED"),
        (b"LD_PRELOAD=/tmp/evil.so\0", "LD_PRELOAD"),
        (b"PYTHONUNBUFFERED=1\0LD_PRELOAD=/tmp/evil.so\0", "PYTHONUNBUFFERED"),
    ],
)
@pytest.mark.parametrize("allow", ["", "   "])
def test_allow_env_empty_or_unset_is_todays_behaviour(block_file, allow, env, expected):
    """名单空 = 今天的行为，一个字节不差：宽匹配 + fail closed。

    逃生阀不是「默认放松一点」。不设它的人拿到的必须还是 #236 那套判词。
    """
    with_valve = run_block(block_file, ESCAPE_SNIPPET.format(shlex.quote(allow)), env)
    without_valve = run_block(block_file, "loader_environment_offender\n", env)
    assert with_valve.strip() == expected
    assert without_valve.strip() == expected


def test_deploy_readme_documents_the_escape_valve():
    """逃生阀只有被写下来才是逃生阀。没人知道的出口等于没有出口。"""
    readme = read(REPO / "deploy" / "README.md")
    assert "ROLLBACK_ALLOW_ENV" in readme


def test_guarded_verification_checks_both_unit_and_process_loader_environment():
    """两个加载器环境检查缺一不可。

    单元定义检查有盲区：`systemctl show -p Environment` 看不见 EnvironmentFile= 指向的
    内容，而这个单元恰好就有一个 EnvironmentFile=。只有进程环境检查能兜住它。
    """
    body = function_body(
        shared_block(read(ROLLBACK_SH)), "verify_guarded_fastapi_runtime"
    )
    assert "verify_unit_loader_environment" in body, "只查了进程环境，单元定义没查"
    assert "verify_process_loader_environment" in body, (
        "只查了单元定义，EnvironmentFile 的盲区没人兜"
    )
    assert "EnvironmentFile" in unit_directives(FASTAPI_UNIT_FILE), (
        "单元不再有 EnvironmentFile，这条盲区论证要重写"
    )


# --------------------------------------------------------------------------
# 8. 脚本印出来的「下一步」必须是能照抄的
# --------------------------------------------------------------------------


def test_deploy_scripts_never_point_at_a_server_side_copy_of_themselves():
    """`/opt/dawnop-dawn/` 下没有任何 `.sh`，别叫人去那里跑一个不存在的文件。

    `deploy.sh` 只装 `backend-dawn.jar` 与 `lib/`，仓库里没有任何一步把这些脚本装到服务器上
    （`deploy/README.md`「一」那条警告说的就是这件事）。回滚脚本结尾曾经印着
    `sudo bash /opt/dawnop-dawn/return-to-dawn.sh`，那是回滚成功之后立刻打印的一行，
    正是人在事故中最可能直接照抄的那行，而照抄的结果是 No such file or directory。
    """
    scripts = sorted((REPO / "backend-dawn" / "deploy").glob("*.sh"))
    assert len(scripts) >= 3, f"部署脚本的位置变了：{[p.name for p in scripts]}"
    for path in scripts:
        found = re.findall(r"/opt/dawnop-dawn/\S*\.sh", read(path))
        assert not found, f"{path.name} 指向服务器上不存在的脚本副本：{found}"


@pytest.mark.parametrize(
    ("path", "other"), [(ROLLBACK_SH, RETURN_SH), (RETURN_SH, ROLLBACK_SH)]
)
def test_each_script_ends_by_pointing_at_the_pipe_over_ssh_form(path, other):
    """结尾的「下一步」印的是 README 里那条正规调用形式，而且指向的是**对方**。

    脚本跑在服务器上，读它输出的人站在本地：提示得说清「从本地仓库 pipe 过去」，
    否则那行照抄不动。指向对方也是判词的一部分：回滚之后要给的是回切，反过来同理。
    """
    tail = read(path).rsplit("\necho\n", 1)[-1]
    assert re.search(
        rf"^echo \"\s*ssh <user>@<server> 'sudo bash -s' "
        rf"< backend-dawn/deploy/{re.escape(other.name)}\"$",
        tail,
        re.M,
    ), f"{path.name} 结尾没有指向 {other.name} 的 pipe-over-ssh 调用形式"


# --------------------------------------------------------------------------
# 关键顺序：核验必须在动 nginx 之前
# --------------------------------------------------------------------------


def test_rollback_verifies_runtime_before_touching_nginx():
    """第 3 步核验、第 4 步才改 nginx。

    「进程已经起来」和「流量还没切过去」之间的这个窗口，是唯一能既看得见真实进程环境
    （补上 EnvironmentFile 盲区）、又还来得及不切流的时刻。核验挪到 nginx 之后，盲区
    就没人兜了：等到发现 .env 里有一行 PYTHONPATH，公网流量已经在那个进程上了。
    """
    text = read(ROLLBACK_SH)
    verify_at = next(
        i
        for i, line in code_lines(text)
        if re.match(r"\s*verify_guarded_fastapi_runtime\s*$", line)
    )
    for later in ("sed -i", "systemctl reload nginx", 'cp -a "$CONF" "$BACKUP"'):
        assert verify_at < line_index(text, later), f"{later} 跑在核验之前了"


def test_rollback_places_the_sentinel_before_starting_the_process():
    """守卫是进程级闩存：起来之后才放的哨兵，这一代进程看不见。"""
    text = read(ROLLBACK_SH)
    assert line_index(text, ': > "$FILE_ROUTE_SENTINEL"') < line_index(
        text, 'systemctl restart "$FASTAPI_UNIT"'
    )
    assert 'systemctl enable --now "$FASTAPI_UNIT"' not in text, (
        "enable --now 对已在跑的服务什么都不做，读到哨兵的就不是新进程了"
    )
