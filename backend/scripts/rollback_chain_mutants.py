#!/usr/bin/env python3
"""回滚安全链的负控：给每条断言配一个能让它转红的 production mutant。

「从没漏过东西」和「从没看过东西」输出一样。tests/test_rollback_chain.py 里有大量
结构性断言（脚本文本的顺序、共享块的逐字节一致、常量与 systemd 单元对齐），这类断言
最容易写成永远绿的样子——匹配一个恰好总在的字符串，或者干脆匹配了个空集。

这个脚本把每个 mutant 打进**生产文件**，跑一遍测试，然后要求：
  1. 至少有一个测试红了（断言真的守着这处代码），且
  2. 红的恰好是预期的那些（断言指向的是它声称守着的东西，不是碰巧被别的连坐）。

用法：
    python3 backend/scripts/rollback_chain_mutants.py            # 全部
    python3 backend/scripts/rollback_chain_mutants.py --only latch-bypass
    python3 backend/scripts/rollback_chain_mutants.py --list

不进 CI（它会临时改生产文件，跑完还原）。跑之前树必须是干净的——中断时按 git 还原。
"""

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
TEST_FILE = "tests/test_rollback_chain.py"

DB_IDENTITY = BACKEND / "app" / "core" / "db_identity.py"
PROCESS_GUARD = BACKEND / "app" / "core" / "process_guard.py"
ROLLBACK_API = BACKEND / "app" / "api" / "rollback.py"
MAIN = BACKEND / "app" / "main.py"
ROLLBACK_SH = REPO / "backend-dawn" / "deploy" / "rollback-to-fastapi.sh"
RETURN_SH = REPO / "backend-dawn" / "deploy" / "return-to-dawn.sh"
DAWN_UNIT = REPO / "backend-dawn" / "deploy" / "dawnop-dawn.service"
FASTAPI_UNIT = REPO / "deploy" / "dawnop-backend.service"
DEPLOY_README = REPO / "deploy" / "README.md"


def sub(old: str, new: str) -> Callable[[str], str]:
    def apply(text: str) -> str:
        if old not in text:
            raise SystemExit(f"mutant 的锚点没了，请更新脚本：{old!r}")
        return text.replace(old, new, 1)

    return apply


def sub_all(old: str, new: str) -> Callable[[str], str]:
    """全部替换。`sub` 只换第一处，对「文档里这个名字必须出现」这类判词没用——
    留下任何一处，断言就还是绿的，于是变异体证明不了任何事。"""

    def apply(text: str) -> str:
        if old not in text:
            raise SystemExit(f"mutant 的锚点没了，请更新脚本：{old!r}")
        return text.replace(old, new)

    return apply


def drop_line(needle: str) -> Callable[[str], str]:
    def apply(text: str) -> str:
        lines = text.splitlines(keepends=True)
        kept = [ln for ln in lines if needle not in ln]
        if len(kept) == len(lines):
            raise SystemExit(f"mutant 的锚点没了，请更新脚本：{needle!r}")
        return "".join(kept)

    return apply


def move_after(needle: str, anchor: str) -> Callable[[str], str]:
    """把含 needle 的那一行挪到含 anchor 的那一行之后。"""

    def apply(text: str) -> str:
        lines = text.splitlines(keepends=True)
        moved = [ln for ln in lines if needle in ln]
        if len(moved) != 1:
            raise SystemExit(f"mutant 的锚点不唯一或没了：{needle!r}")
        rest = [ln for ln in lines if needle not in ln]
        for i, ln in enumerate(rest):
            if anchor in ln:
                rest.insert(i + 1, moved[0])
                return "".join(rest)
        raise SystemExit(f"mutant 找不到落点：{anchor!r}")

    return apply


# 每条：(名字, 说明, 目标文件们, 变形, 预期转红的测试函数名)
#
# 动共享块的 mutant 必须**同时打进两个脚本**。只改一个的话，逐字节比对器会先转红，
# 于是「只红这一条」的证据就糊了——而真实世界里改共享块的人本来就该两边一起改，
# 单改一个是另一个 mutant（shared-block-drift）专门在测的事。
BOTH_SCRIPTS = [ROLLBACK_SH, RETURN_SH]

MUTANTS: list[tuple[str, str, list[Path], Callable[[str], str], set[str]]] = [
    (
        "readme-command-drift",
        "运维手册的核对命令与代码配方漂开（%d:%i 写反）",
        [DEPLOY_README],
        sub("stat -Lc '%d:%i'", "stat -Lc '%i:%d'"),
        {"test_deploy_readme_verification_command_actually_works"},
    ),
    (
        "readme-drops-the-printf-wrapper",
        "手册漏掉 $(...) 那层，尾换行被算进 sha256",
        [DEPLOY_README],
        sub(
            "printf '%s' \"$(stat -Lc '%d:%i' -- /opt/dawnop/data/dawnop.db)\" | sha256sum",
            "stat -Lc '%d:%i' -- /opt/dawnop/data/dawnop.db | sha256sum",
        ),
        {"test_deploy_readme_verification_command_actually_works"},
    ),
    (
        "fingerprint-recipe",
        "指纹配方把 dev:ino 换成 ino:dev（与 stat -Lc '%d:%i' 不再一致）",
        [DB_IDENTITY],
        sub('f"{st.st_dev}:{st.st_ino}"', 'f"{st.st_ino}:{st.st_dev}"'),
        # 配方是三处的共同真相：Python、脚本里的函数、手册里的命令。动一处，三处一起红。
        {
            "test_fingerprint_matches_stat_and_sha256sum",
            "test_fingerprint_recipe_is_dev_colon_ino",
            "test_deploy_readme_verification_command_actually_works",
        },
    ),
    (
        "fingerprint-proc-scan",
        "改回翻 /proc/self/fd 归因（不再在自己 open 的 fd 上 fstat）",
        [DB_IDENTITY],
        sub(
            '    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)\n'
            "    try:\n"
            "        fd = os.open(path, flags)",
            '    _before = set(os.listdir("/proc/self/fd"))\n'
            "    flags = os.O_RDONLY\n"
            "    try:\n"
            "        fd = os.open(path, flags)",
        ),
        {"test_fingerprint_opens_its_own_fd_and_closes_it"},
    ),
    (
        "fingerprint-accepts-anything",
        "不再拒绝非普通文件（目录也能当库）",
        [DB_IDENTITY],
        sub("    if not stat.S_ISREG(st.st_mode):", "    if False:"),
        {"test_fingerprint_rejects_non_regular_file"},
    ),
    (
        "latch-bypass",
        "闩存不再拒绝改口：第二个不同的指纹被静默接受",
        [DB_IDENTITY],
        sub(
            "        if self._fingerprint is not None and self._fingerprint != fingerprint:",
            "        if False:",
        ),
        {"test_latch_rejects_second_different_fingerprint"},
    ),
    (
        "latch-read-unlatched",
        "未闩时 read() 不抛，返回空串",
        [DB_IDENTITY],
        sub("        if self._fingerprint is None:", "        if False:"),
        {"test_latch_read_before_latch_raises"},
    ),
    (
        "establish-expected-after-open",
        "先开库再取期望指纹（把结果当期望，中途换文件抓不出来）",
        [DB_IDENTITY],
        sub(
            "    expected = fingerprint_of_path(path)\n"
            "\n"
            "    opened_file = main_file_resolver(database_url)",
            "    opened_file = main_file_resolver(database_url)\n"
            "    expected = fingerprint_of_path(path)\n",
        ),
        {"test_establish_takes_the_expected_fingerprint_before_opening"},
    ),
    (
        "guard-rereads-disk",
        "进程守卫每次都回头 stat 哨兵（不再是进程级闩存）",
        [PROCESS_GUARD],
        sub(
            "        if self._disabled is None:\n"
            "            self._disabled = os.path.exists(self._sentinel_path)\n"
            "        return self._disabled",
            "        self._disabled = os.path.exists(self._sentinel_path)\n"
            "        return self._disabled",
        ),
        {
            "test_guard_reads_the_sentinel_once_and_never_again",
            "test_guard_absent_sentinel_also_latches",
        },
    ),
    (
        "probe-403-not-404",
        "探针没带对头时回 403（等于确认这个端点存在）",
        [ROLLBACK_API],
        sub("status_code=status.HTTP_404_NOT_FOUND", "status_code=403"),
        {
            "test_probe_404_without_the_header",
            "test_probe_404_with_a_wrong_header",
            "test_probe_404_when_no_secret_is_configured",
        },
    ),
    (
        "probe-empty-secret-open",
        "秘密留空时探针对空头放行",
        [ROLLBACK_API],
        sub("    if not expected:\n        # ", "    if False:\n        # "),
        {"test_probe_404_when_no_secret_is_configured"},
    ),
    (
        "probe-answers-unlatched",
        "未闩时探针照样回指纹（编一个出来）",
        [ROLLBACK_API],
        sub("    if not database_identity_latched():", "    if False:"),
        {"test_probe_503_when_identity_is_not_latched"},
    ),
    (
        "probe-in-schema",
        "探针进 OpenAPI 文档",
        [ROLLBACK_API],
        sub(
            '@router.get("/db-identity", include_in_schema=False)',
            '@router.get("/db-identity")',
        ),
        {"test_probe_is_not_in_the_openapi_schema"},
    ),
    (
        "lifespan-guarded-writes-schema",
        "受守护启动时照样建表（而不是只核定身份）",
        [MAIN],
        sub(
            "        establish_database_identity(settings.database_url)",
            "        establish_database_identity(settings.database_url)\n        init_db()",
        ),
        {"test_lifespan_guarded_establishes_identity_and_leaves_the_schema_alone"},
    ),
    (
        "guard-does-not-guard",
        "文件路由守卫不再拦（哨兵在也放行）",
        [MAIN],
        sub(
            "    if process_file_routes_disabled():\n        raise HTTPException",
            "    if False:\n        raise HTTPException",
        ),
        {
            "test_guarded_process_refuses_file_routes",
            "test_guarded_file_route_refusal_outranks_authentication",
        },
    ),
    (
        "shared-block-drift",
        "回切脚本的共享块里多一个空格（逐字节比对唯一能抓的那类漂移）",
        [RETURN_SH],
        sub('fatal() {\n  echo "!!! $*" >&2', 'fatal() {\n   echo "!!! $*" >&2'),
        {"test_shared_fastapi_verification_block_is_identical"},
    ),
    (
        "shared-block-emptied",
        "共享块被掏空（比对器还在，但没东西可比）",
        [RETURN_SH],
        None,  # 占位，见下方特判
        {"test_shared_fastapi_verification_block_is_identical"},
    ),
    (
        "loader-check-only-unit",
        "只查单元定义的加载器环境，不查进程环境（EnvironmentFile 的盲区没人兜）",
        BOTH_SCRIPTS,
        drop_line('verify_process_loader_environment "$pid" ||\n'),
        {"test_guarded_verification_checks_both_unit_and_process_loader_environment"},
    ),
    (
        "loader-offender-drops-ld",
        "加载器变量判词只认 PYTHON*，放过 LD_PRELOAD",
        BOTH_SCRIPTS,
        sub("/^(LD_|PYTHON)/", "/^(PYTHON)/"),
        # 逃生阀那三条也用 LD_* 当样本（放行判词对两个前缀族必须一视同仁），
        # 所以这个 mutant 连坐它们——不是判词糊了，是 LD_ 真的被这四条一起守着。
        {
            "test_loader_environment_offender",
            "test_allow_env_releases_only_the_exact_variable_name",
            "test_allow_env_echoes_the_released_variable_name",
            "test_allow_env_empty_or_unset_is_todays_behaviour",
        },
    ),
    # ---- 逃生阀 ROLLBACK_ALLOW_ENV ----
    # 一个「放行」的判词有四种烂法：放宽成前缀/子串/glob、放行了却不留痕、
    # 名单一非空就整体开门、以及「第一个 offender 恰好在名单里就当整条流干净」。
    # 每一种在这里都有一个变异体。
    (
        "allow-env-prefix-match",
        "白名单退化成前缀匹配（PYTHONUNBUFFERED 顺带放行 PYTHONUNBUFFERED_EXTRA）",
        BOTH_SCRIPTS,
        sub('if [ "$entry" = "$name" ]; then', 'if [[ "$name" == "$entry"* ]]; then'),
        {"test_allow_env_releases_only_the_exact_variable_name"},
    ),
    (
        "allow-env-substring-match",
        "白名单退化成子串匹配（PATH 顺带放行 PYTHONPATH）",
        BOTH_SCRIPTS,
        sub('if [ "$entry" = "$name" ]; then', 'if [[ "$name" == *"$entry"* ]]; then'),
        {"test_allow_env_releases_only_the_exact_variable_name"},
    ),
    (
        "allow-env-glob-match",
        "白名单退化成 glob（PYTHON* 一条放行全部）",
        BOTH_SCRIPTS,
        sub('if [ "$entry" = "$name" ]; then', 'if [[ "$name" == $entry ]]; then'),
        {"test_allow_env_releases_only_the_exact_variable_name"},
    ),
    (
        "allow-env-releases-silently",
        "放行了但不回显被放行的名字（无声接受 = 逃生阀白装）",
        BOTH_SCRIPTS,
        drop_line("[escape] ROLLBACK_ALLOW_ENV 放行了加载器环境变量"),
        {"test_allow_env_echoes_the_released_variable_name"},
    ),
    (
        "allow-env-first-offender-wins",
        "awk 又自己 exit：第一个 offender 恰好被放行，后面的就没人看了",
        BOTH_SCRIPTS,
        sub("/^(LD_|PYTHON)/ { print $1 }", "/^(LD_|PYTHON)/ { print $1; exit }"),
        {"test_allow_env_releases_only_the_exact_variable_name"},
    ),
    (
        "allow-env-non-empty-list-opens-the-gate",
        "名单一非空就整体放行（列了一个名字等于关掉这道检查）",
        BOTH_SCRIPTS,
        sub(
            "  done\n  return 1\n}\n\nloader_environment_offender() {",
            '  done\n  [ -n "$ROLLBACK_ALLOW_ENV" ]\n}\n\nloader_environment_offender() {',
        ),
        # 也红「名单为空 = 今天的行为」那条：那条的参数里有一个纯空白的名单，
        # 而 `[ -n "   " ]` 是真——「有没有设」和「设了什么」是两个问题。
        {
            "test_allow_env_releases_only_the_exact_variable_name",
            "test_allow_env_empty_or_unset_is_todays_behaviour",
        },
    ),
    (
        "allow-env-always-open",
        "放行判词恒真：不设名单也 fail open",
        BOTH_SCRIPTS,
        sub("  local name=$1 entry", "  return 0\n  local name=$1 entry"),
        {
            "test_loader_environment_offender",
            "test_allow_env_releases_only_the_exact_variable_name",
            "test_allow_env_empty_or_unset_is_todays_behaviour",
        },
    ),
    (
        "escape-valve-undocumented",
        "逃生阀从运维手册里消失（没人知道的出口等于没有出口）",
        [DEPLOY_README],
        sub_all("ROLLBACK_ALLOW_ENV", "ROLLBACK_UNDOCUMENTED_KNOB"),
        {"test_deploy_readme_documents_the_escape_valve"},
    ),
    (
        "interpreter-check-gone",
        "不再比 /proc/$PID/exe 与钉住的解释器",
        BOTH_SCRIPTS,
        sub('  actual=$(resolve_path "/proc/$pid/exe")', "  actual=$pinned"),
        {"test_pinned_interpreter_is_compared_through_proc_exe"},
    ),
    (
        "fastapi-verified-cache",
        "把 FASTAPI_VERIFIED 缓存加回来",
        [RETURN_SH],
        sub(
            "  verify_guarded_fastapi_runtime\n  exit 1",
            '  [ "${FASTAPI_VERIFIED:-}" = 1 ] && exit 1\n'
            "  verify_guarded_fastapi_runtime\n"
            "  FASTAPI_VERIFIED=1\n"
            "  exit 1",
        ),
        {"test_no_cached_fastapi_verification"},
    ),
    (
        "keep-fastapi-skips-verify",
        "「保留 FastAPI」的路径不再重新核验",
        [RETURN_SH],
        drop_line("  verify_guarded_fastapi_runtime\n"),
        {
            "test_keeping_fastapi_reruns_the_full_verification",
            "test_both_directions_verify_the_same_way",
        },
    ),
    (
        "verify-after-nginx",
        "把核验挪到改 nginx 之后",
        [ROLLBACK_SH],
        move_after("verify_guarded_fastapi_runtime\n", "systemctl reload nginx"),
        {"test_rollback_verifies_runtime_before_touching_nginx"},
    ),
    (
        "sentinel-after-start",
        "哨兵在进程起来之后才放（这一代进程看不见）",
        [ROLLBACK_SH],
        move_after(': > "$FILE_ROUTE_SENTINEL"', 'systemctl restart "$FASTAPI_UNIT"'),
        {"test_rollback_places_the_sentinel_before_starting_the_process"},
    ),
    (
        "no-reverify-after-reload",
        "回切 reload 之后不再重新核验 Dawn",
        [RETURN_SH],
        drop_line('verify_dawn_runtime || keep_fastapi_and_bail "reload 之后'),
        {"test_return_to_dawn_reverifies_after_reload"},
    ),
    (
        "no-public-probe-after-reload",
        "回切 reload 之后不做公网探活",
        [RETURN_SH],
        sub(
            'probe_suite https "$SITE_ORIGIN" "dawn-public" \\\n',
            'true "$SITE_ORIGIN" "dawn-public" \\\n',
        ),
        {"test_return_to_dawn_probes_public_surface_after_reload"},
    ),
    (
        "health-only-liveness",
        "Dawn 侧探活缩回只有一个 /api/health",
        [RETURN_SH],
        None,  # 占位，见下方特判
        {
            "test_dawn_liveness_is_more_than_one_health_call",
            "test_dawn_liveness_asserts_authentication_is_wired",
            "test_dawn_liveness_distinguishes_itself_from_the_guarded_fastapi",
        },
    ),
    (
        "dawn-runtime-skips-loader-checks",
        "Dawn 侧不再查加载器环境（两个方向验的不是同一组东西）",
        [RETURN_SH],
        drop_line('verify_unit_loader_environment "$DAWN_UNIT" || return 1'),
        {"test_dawn_runtime_verification_reuses_the_shared_predicates"},
    ),
    (
        "argv-drift-from-unit",
        "共享块里的 argv 与 systemd 单元漂开",
        BOTH_SCRIPTS,
        sub('--host 127.0.0.1 --port 8000"', '--host 0.0.0.0 --port 8000"'),
        {"test_argv_constant_is_execstart_modulo_the_interpreter_prefix"},
    ),
    (
        "argv-copied-from-execstart",
        "argv 照抄 ExecStart=，丢掉 shebang 会插进 argv[0] 的解释器（2026-08-14 的真实缺陷）",
        BOTH_SCRIPTS,
        sub(
            'FASTAPI_ARGV="/opt/dawnop/backend/.venv/bin/python3 '
            "/opt/dawnop/backend/.venv/bin/uvicorn",
            'FASTAPI_ARGV="/opt/dawnop/backend/.venv/bin/uvicorn',
        ),
        # 只红这一条：ARGV 变回 == ExecStart，所以「由单元派生」那条仍然成立。
        {"test_argv_constant_starts_with_the_pinned_interpreter"},
    ),
    (
        "user-drift-from-unit",
        "共享块里的运行身份与 systemd 单元漂开（改回历史上那个错的 dawn）",
        BOTH_SCRIPTS,
        sub('FASTAPI_USER="dawnop"', 'FASTAPI_USER="dawn"'),
        {"test_fastapi_identity_constants_match_the_systemd_unit"},
    ),
    (
        "uid-check-uses-real-not-effective",
        "身份核验读真实 UID 而不是有效 UID",
        BOTH_SCRIPTS,
        sub("'/^Uid:/ { print $3 }'", "'/^Uid:/ { print $2 }'"),
        {"test_unit_identity_check_covers_unit_argv_cwd_uid_gid"},
    ),
    # ---- 脚本印出来的「下一步」 ----
    (
        "tail-points-at-a-server-side-copy",
        "结尾的「下一步」改回 /opt/dawnop-dawn/ 下那个不存在的脚本副本",
        BOTH_SCRIPTS,
        None,  # 占位，见下方特判
        {
            "test_deploy_scripts_never_point_at_a_server_side_copy_of_themselves",
            "test_each_script_ends_by_pointing_at_the_pipe_over_ssh_form",
        },
    ),
    (
        "tail-points-at-itself",
        "结尾的「下一步」指向自己而不是对方（回滚完印回滚）",
        BOTH_SCRIPTS,
        None,  # 占位，见下方特判
        {"test_each_script_ends_by_pointing_at_the_pipe_over_ssh_form"},
    ),
    # ---- 加固不对称：注释与手册说的必须是实测 ----
    (
        "hardening-comment-claims-parity",
        "Dawn 单元的注释又宣称与 FastAPI 单元一致（#251 那条假陈述）",
        [DAWN_UNIT],
        sub(
            "# hardening. This is NOT mirrored by the rollback target:"
            " dawnop-backend.service",
            "# hardening (mirrors the FastAPI unit). dawnop-backend.service",
        ),
        {"test_hardening_comment_matches_what_the_two_units_actually_carry"},
    ),
    (
        "fastapi-unit-silently-hardened",
        "给 FastAPI 单元补上加固，但注释与手册还在讲不对称",
        [FASTAPI_UNIT],
        sub("Restart=on-failure", "NoNewPrivileges=true\nRestart=on-failure"),
        {"test_hardening_comment_matches_what_the_two_units_actually_carry"},
    ),
    (
        "dawn-unit-loses-hardening",
        "Dawn 单元的加固被删光，注释与手册还在讲它有四条",
        [DAWN_UNIT],
        None,  # 占位，见下方特判
        {"test_hardening_comment_matches_what_the_two_units_actually_carry"},
    ),
    (
        "readme-hides-the-unhardened-rollback-target",
        "回滚一节不再说「回滚落到的是一个未加固的进程」",
        [DEPLOY_README],
        sub_all("未加固", "照常加固"),
        {"test_deploy_readme_warns_that_the_rollback_target_is_unhardened"},
    ),
    (
        "readme-drops-the-missing-directive-list",
        "回滚一节不再点名 FastAPI 单元缺的是哪几条",
        [DEPLOY_README],
        sub(
            "有四条：`NoNewPrivileges`、\n"
            "> `ProtectSystem`（配 `ReadWritePaths=/opt/dawnop/data`）、`ProtectHome`、"
            "`PrivateTmp`。",
            "有若干条。",
        ),
        {"test_deploy_readme_warns_that_the_rollback_target_is_unhardened"},
    ),
]


def _empty_the_shared_block(text: str) -> str:
    start = "# >>> shared FastAPI verification block >>>"
    end = "# <<< shared FastAPI verification block <<<"
    i, j = text.index(start), text.index(end)
    return text[: i + len(start)] + "\n" + text[j:]


def _health_only_probes(text: str) -> str:
    return re.sub(
        r"^DAWN_PROBES=\(\n.*?^\)$",
        'DAWN_PROBES=(\n  "GET /api/health 200"\n)',
        text,
        count=1,
        flags=re.M | re.S,
    )


def _tail_points_at_a_server_side_copy(text: str) -> str:
    """把结尾的「下一步」改回 #250 之前那个不存在的路径。"""
    new, n = re.subn(
        r'echo "  (回切|再次回滚)（[^）]*）："\n'
        r"echo \"    ssh <user>@<server> 'sudo bash -s' "
        r'< backend-dawn/deploy/(\S+\.sh)"',
        lambda m: f'echo "  {m.group(1)}：sudo bash /opt/dawnop-dawn/{m.group(2)}"',
        text,
    )
    if n != 1:
        raise SystemExit("mutant 的锚点不唯一或没了：结尾的「下一步」那两行")
    return new


def _tail_points_at_itself(text: str) -> str:
    """让结尾的「下一步」指向自己。`"` 结尾把它与文件头注释里的同款调用形式区分开。"""
    names = ("rollback-to-fastapi.sh", "return-to-dawn.sh")

    def swap(m: "re.Match[str]") -> str:
        other = names[1] if m.group(1) == names[0] else names[0]
        return m.group(0).replace(m.group(1), other)

    new, n = re.subn(r'< backend-dawn/deploy/(\S+\.sh)"', swap, text)
    if n != 1:
        raise SystemExit("mutant 的锚点不唯一或没了：结尾的 pipe-over-ssh 行")
    return new


def _strip_dawn_hardening(text: str) -> str:
    pattern = re.compile(r"(NoNewPrivileges|ProtectSystem|ProtectHome|PrivateTmp)=")
    lines = text.splitlines(keepends=True)
    kept = [ln for ln in lines if not pattern.match(ln)]
    if len(kept) == len(lines):
        raise SystemExit("mutant 的锚点没了：Dawn 单元的加固指令")
    return "".join(kept)


SPECIAL = {
    "shared-block-emptied": _empty_the_shared_block,
    "health-only-liveness": _health_only_probes,
    "tail-points-at-a-server-side-copy": _tail_points_at_a_server_side_copy,
    "tail-points-at-itself": _tail_points_at_itself,
    "dawn-unit-loses-hardening": _strip_dawn_hardening,
}


def run_tests() -> set[str]:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            TEST_FILE,
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
    )
    failed = set()
    for line in proc.stdout.splitlines():
        m = re.match(r"FAILED .*::([A-Za-z0-9_]+)", line)
        if m:
            failed.add(m.group(1))
    if proc.returncode == 0 and not failed:
        return set()
    if proc.returncode not in (0, 1):
        # 收集期错误之类：整个文件红了，这不算「某条断言守住了」
        print(proc.stdout[-3000:])
        raise SystemExit("pytest 以非 0/1 退出，mutant 打坏了别的东西")
    return failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for name, why, paths, _t, _expect in MUTANTS:
            print(f"{name:34} {','.join(p.name for p in paths):40} {why}")
        return 0

    chosen = [m for m in MUTANTS if not args.only or m[0] in args.only]
    if not chosen:
        raise SystemExit("--only 没匹配到任何 mutant")

    print("== 基线（未变异）==")
    baseline = run_tests()
    if baseline:
        raise SystemExit(f"基线就有红的，先修：{sorted(baseline)}")
    print("   全绿\n")

    bad = []
    for name, why, paths, transform, expect in chosen:
        originals = {path: path.read_bytes() for path in paths}
        apply = SPECIAL.get(name) or transform
        try:
            for path, original in originals.items():
                path.write_text(apply(original.decode("utf-8")), encoding="utf-8")
            failed = run_tests()
        finally:
            for path, original in originals.items():
                path.write_bytes(original)

        ok = failed == expect
        print(f"[{'RED ' if failed else 'GREEN'}] {name}: {why}")
        print(f"        文件 {', '.join(str(p.relative_to(REPO)) for p in paths)}")
        print(f"        期望转红 {sorted(expect)}")
        print(f"        实测转红 {sorted(failed)}")
        if not ok:
            bad.append(name)
            print("        ^^ 不符")
        print()

    if bad:
        print(f"不合格的 mutant：{bad}")
        return 1
    print(f"全部 {len(chosen)} 个 mutant 都只红了它该红的那些。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
