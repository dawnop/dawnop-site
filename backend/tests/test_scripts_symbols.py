"""`backend/scripts/` 里引用的名字必须真的存在。

为什么需要这条：`sweep_qiniu_orphans.py` 与 `wipe_qiniu.py` 都写着
`qiniu_client._bucket_manager()`，而 `_bucket_manager` 住在 `kodo` 子模块里、
包的 `__init__.py` 从没把它 re-export 出来。于是从「七牛封装由单文件拆成包」
那天起，这两个脚本就再也跑不起来了，报 AttributeError。

没人发现，是因为运维脚本是全仓被执行得最少的代码：它们不在任何测试里、
不在 CI 里、平时也没人跑。等到真要清理孤儿对象的那天才发现工具是坏的，
而那天通常就是最不想调试工具的一天。

这条守卫替代不了「跑一遍」，它只保证名字对得上——但名字对不上正是这两个
脚本唯一的毛病，而这类毛病 import 一下就能全部暴露。

两个方向各查一次：
- `test_script_imports_cleanly` 执行脚本的模块级代码（`if __name__ ==
  "__main__"` 不会触发），所以 `from x import y` 里 y 不存在会当场炸。
- `test_module_attribute_references_resolve` 扫 AST 里 `模块.名字` 的形式，
  盖住那些藏在函数体里、import 不到的引用——也就是原来那个缺陷的形状。
"""

import ast
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
SCRIPTS = sorted((BACKEND / "scripts").glob("*.py"))


def _script_ids(p: Path) -> str:
    return p.name


assert SCRIPTS, "backend/scripts/ 里一个 .py 都没有，这条守卫在空跑"


def _load(script: Path):
    """按模块加载脚本，`__name__` 不是 __main__ 所以入口不会执行。"""
    name = f"_script_under_test_{script.stem}"
    spec = importlib.util.spec_from_file_location(name, script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


@pytest.mark.parametrize("script", SCRIPTS, ids=_script_ids)
def test_script_imports_cleanly(script: Path) -> None:
    _load(script)


def _imported_modules(tree: ast.Module) -> dict:
    """脚本里绑定到某个模块对象的局部名 -> 模块全名。

    只收 `from a.b import c` 形式绑出来的 `c`（c 是子模块时才有意义）与
    `import a.b as c`。裸 `import a.b` 绑的是顶层名 `a`，一并记上。
    """
    bound = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bound[alias.asname] = alias.name
                else:
                    bound[alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                local = alias.asname or alias.name
                bound[local] = f"{node.module}.{alias.name}"
    return bound


@pytest.mark.parametrize("script", SCRIPTS, ids=_script_ids)
def test_module_attribute_references_resolve(script: Path) -> None:
    tree = ast.parse(script.read_text(encoding="utf-8"))
    bound = _imported_modules(tree)

    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        target = bound.get(node.value.id)
        if target is None:
            continue
        try:
            module = importlib.import_module(target)
        except ImportError:
            # 名字绑的不是模块（`from app.config import settings` 那种），
            # 属性归对象管，不在这条守卫的射程内。
            continue
        assert hasattr(module, node.attr), (
            f"{script.name} 引用了 {node.value.id}.{node.attr}，"
            f"但 {target} 上没有这个名字"
        )
        checked += 1

    # 每个脚本都至少要有一处被真的检查过，否则这条断言在给空集合背书。
    # 六个脚本每个都 import app 下的模块并调它们的东西；哪天不是了，
    # 该改的是这条注释，不是把断言删掉。
    assert checked > 0, f"{script.name} 里没有任何 模块.名字 引用被检查到"
