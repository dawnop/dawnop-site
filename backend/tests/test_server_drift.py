"""`scripts/check_server_drift.py` 的判词。

这个脚本本身要 ssh 上生产，跑不进 CI。但它做判断的那部分（`compare`）是纯函数，
喂两张假表就能全测。三个方向各有一条判词，各自被 `scripts/server_drift_mutants.py`
里一个变异体守着——2026-08-14 那次故障里，三个方向的漂移各出现了一次，
少测哪一个方向，对应的那类漂移就还是隐形的。
"""

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_server_drift.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_server_drift", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


drift = _load()


# --------------------------------------------------------------------------
# compare 的三个方向
# --------------------------------------------------------------------------


def test_identical_sides_report_nothing():
    """双向判词：没有差异时必须是**空**列表。

    少了这条，`compare` 可以写成「无脑返回一条抱怨」而其余三条测试照绿。
    """
    table = {"g": {"a": "aa", "b": "bb"}}
    assert drift.compare(table, {"g": {"a": "aa", "b": "bb"}}) == []


def test_missing_on_server_is_reported():
    """仓库有、服务器没有。`#236` 的三个文件走的就是这条。"""
    problems = drift.compare({"g": {"a": "aa"}}, {"g": {}})
    assert len(problems) == 1
    assert "缺 a" in problems[0]


def test_extra_on_server_is_reported():
    """服务器有、仓库没有。陈旧的 rollback 脚本副本走的是这条。

    `stale-scripts` 那一组的期望是空表，所以它**只能**靠这个方向被发现。
    """
    problems = drift.compare({"g": {}}, {"g": {"rollback-to-fastapi.sh": "aa"}})
    assert len(problems) == 1
    assert "多出 rollback-to-fastapi.sh" in problems[0]


def test_same_name_different_content_is_reported():
    """两边都有但内容不同。`User=dawn` vs `User=dawnop` 走的是这条。"""
    problems = drift.compare({"g": {"a": "aa"}}, {"g": {"a": "bb"}})
    assert len(problems) == 1
    assert "内容不同" in problems[0]


def test_a_group_only_the_server_knows_about_still_gets_compared():
    """服务器报了一个 manifest 里没有的组，也不许被静默丢掉。"""
    problems = drift.compare({}, {"surprise": {"x": "aa"}})
    assert len(problems) == 1
    assert "surprise" in problems[0]


# --------------------------------------------------------------------------
# manifest 与远端脚本必须说的是同一件事
# --------------------------------------------------------------------------


@pytest.mark.parametrize("group", sorted(drift.GROUPS))
def test_every_manifest_group_is_emitted_by_the_remote_script(group):
    """manifest 里声明的每一组，远端脚本都要真的产出它。

    否则那一组的期望表恒等于「服务器上什么都没有」，于是仓库里的每个文件都会被报成
    「服务器上缺」，或者反过来整组恒绿——两种都是在没看的情况下给出答案。
    """
    remote = drift.REMOTE_SCRIPT
    # 组名要么整个出现（unit:$u 是拼出来的，故也接受它的后半段）
    assert group in remote or group.split(":", 1)[-1] in remote, (
        f"{group} 在 REMOTE_SCRIPT 里没有对应的 emit"
    )


def test_remote_script_excludes_pycache():
    """生产上 55 个源文件旁边有 55 个 .pyc。不排除的话每次都会报 55 处假漂移，

    而一个天天喊狼来了的检查器，和没有这个检查器是一样的。
    """
    assert "__pycache__" in drift.REMOTE_SCRIPT
    assert "*.pyc" in drift.REMOTE_SCRIPT


def test_repo_side_maps_backend_app_onto_the_server_layout():
    """仓库 `backend/app/x.py` 对应服务器 `/opt/dawnop/backend/app/x.py`，

    所以键必须是 `app/x.py`——带上 `backend/` 前缀的话两边一个都对不上，
    结果是「全部缺失 + 全部多余」，噪声大到没人会去读。
    """
    expected = drift.repo_side()
    keys = expected["fastapi-app"]
    assert keys, "fastapi-app 组是空的，git ls-files 没找到东西？"
    assert all(k.startswith("app/") for k in keys), sorted(keys)[:5]
    assert "app/main.py" in keys


def test_the_three_files_that_were_missing_in_production_are_in_the_manifest():
    """#236 加的三个文件必须落在被比对的集合里。

    它们没上生产整整五周，而回滚链的全部核验都建立在它们身上。
    """
    keys = drift.repo_side()["fastapi-app"]
    for f in (
        "app/api/rollback.py",
        "app/core/db_identity.py",
        "app/core/process_guard.py",
    ):
        assert f in keys, f


# --------------------------------------------------------------------------
# #263 的库备份：脚本 + service + timer
# --------------------------------------------------------------------------


def _server_view() -> dict[str, dict[str, str]]:
    """一台「和仓库完全一致」的假服务器：照抄仓库侧的期望表。

    下面三条各自只动它一处，于是被报出来的那一条差异就是被动的那一处，
    不掺别的噪声。用真的 `repo_side()` 当底子是有意的：这样「备份那几样根本没进
    manifest」会让这三条一起红，而不是让它们对着一张手写的假表自说自话地绿。
    """
    return {group: dict(files) for group, files in drift.repo_side().items()}


def test_the_backup_pieces_installed_by_263_are_in_the_manifest():
    """#263 往服务器上装了三样东西，三样都要被比对，且比的是仓库里那份正本。

    键对了但哈希取自别的文件，检查器一样是瞎的——它会拿一个永远对不上的值去比，
    或者更糟，拿一个碰巧不变的值去比。
    """
    expected = drift.repo_side()
    for group, name, repo_rel in (
        ("unit:dawnop-backup", "dawnop-backup.service", "deploy/dawnop-backup.service"),
        ("unit:dawnop-backup", "dawnop-backup.timer", "deploy/dawnop-backup.timer"),
        ("opt-scripts", "backup-db.sh", "deploy/backup-db.sh"),
    ):
        assert name in expected[group], f"{group} 里没有 {name}"
        assert expected[group][name] == drift.sha256_of(REPO / repo_rel), repo_rel


def test_repo_side_strips_the_deploy_prefix_for_installed_files():
    """仓库 `deploy/backup-db.sh` 装到服务器上是 `/opt/dawnop/backup-db.sh`，

    `deploy/dawnop-backup.timer` 装成 `/etc/systemd/system/dawnop-backup.timer`。
    两边都只剩基名，所以键上不许留 `deploy/`——留着就是「全缺 + 全多」的噪声。
    """
    expected = drift.repo_side()
    assert set(expected["unit:dawnop-backup"]) == {
        "dawnop-backup.service",
        "dawnop-backup.timer",
    }
    assert set(expected["opt-scripts"]) == {"backup-db.sh"}


def test_backup_timer_missing_on_server_is_reported():
    """只装了 service、timer 没上去：备份永远不会自己跑，而表面上「装好了」。"""
    actual = _server_view()
    del actual["unit:dawnop-backup"]["dawnop-backup.timer"]
    problems = drift.compare(drift.repo_side(), actual)
    assert problems == ["unit:dawnop-backup: 服务器上缺 dawnop-backup.timer"]


def test_an_unknown_script_under_opt_dawnop_is_reported():
    """服务器上冒出来一个仓库里没有的脚本——手改留下的东西走的就是这条。"""
    actual = _server_view()
    actual["opt-scripts"]["restore-db.sh"] = "aa"
    problems = drift.compare(drift.repo_side(), actual)
    assert problems == ["opt-scripts: 服务器上多出 restore-db.sh（仓库里没有）"]


def test_backup_script_edited_on_the_server_is_reported():
    """两边都有 `backup-db.sh`，但服务器上那份被就地改过。

    这是备份最可能的坏法：有人上去调了 KEEP 或者改了路径，仓库那份纹丝不动。
    """
    actual = _server_view()
    actual["opt-scripts"]["backup-db.sh"] = "0" * 64
    problems = drift.compare(drift.repo_side(), actual)
    assert len(problems) == 1
    assert problems[0].startswith("opt-scripts: backup-db.sh 内容不同")


def test_unit_files_compared_are_the_ones_the_rollback_chain_reads():
    """比对的必须是回滚脚本常量所对齐的那两个单元文件。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r'"deploy"\s*/\s*"dawnop-backend\.service"', src)
    assert re.search(r'"backend-dawn"\s*/\s*"deploy"\s*/\s*"dawnop-dawn\.service"', src)
