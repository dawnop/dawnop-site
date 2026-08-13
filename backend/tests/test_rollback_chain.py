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
    proc = subprocess.run(
        ["bash", "-c", f". {shlex.quote(str(block_file))}\n{snippet}"],
        input=stdin,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    return proc.stdout.decode()


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
    reload_at = line_index(text, "systemctl reload nginx")
    after = [
        i
        for i, line in code_lines(text)
        if i > reload_at and re.match(r"\s*verify_dawn_runtime\b", line)
    ]
    assert after, "reload 之后没有任何一次重新核验——切回去了不等于健康"


def test_return_to_dawn_probes_public_surface_after_reload():
    text = read(RETURN_SH)
    reload_at = line_index(text, "systemctl reload nginx")
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
    assert f'FASTAPI_ARGV="{unit["ExecStart"]}"' in block
    assert f'FASTAPI_WORKDIR="{unit["WorkingDirectory"]}"' in block
    assert f'FASTAPI_USER="{unit["User"]}"' in block
    assert f'FASTAPI_GROUP="{unit["Group"]}"' in block


def test_dawn_identity_constants_match_the_systemd_unit():
    text = read(RETURN_SH)
    unit = unit_directives(DAWN_UNIT_FILE)
    assert f'DAWN_ARGV="{unit["ExecStart"]}"' in text
    assert f'DAWN_WORKDIR="{unit["WorkingDirectory"]}"' in text
    assert f'DAWN_USER="{unit["User"]}"' in text
    assert f'DAWN_GROUP="{unit["Group"]}"' in text


def test_fastapi_constants_live_only_in_the_shared_block():
    """FastAPI 侧的常量不许在块外再定义一份——那正是两边漂开的起点。"""
    for path in (ROLLBACK_SH, RETURN_SH):
        text = read(path)
        outside = text.replace(shared_block(text), "")
        assert not re.search(r"^FASTAPI_[A-Z_]+=", outside, re.M), path.name


def test_both_directions_verify_the_same_way():
    for path in (ROLLBACK_SH, RETURN_SH):
        assert "verify_guarded_fastapi_runtime" in read(path)


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
    body = function_body(shared_block(read(ROLLBACK_SH)), "verify_pinned_interpreter")
    assert "/proc/$pid/exe" in body
    assert "resolve_path" in body, "两边都要 readlink -f 之后再比"
    resolve = function_body(shared_block(read(ROLLBACK_SH)), "resolve_path")
    assert "readlink -f" in resolve


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
