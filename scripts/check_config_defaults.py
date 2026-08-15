#!/usr/bin/env python3
"""两个后端读同一个 .env，那它们对「没写这个键」的理解也必须是同一个。

**为什么存在。** `backend-dawn/src/main.dawn` 里有一句注释：

    # defaults mirror app/config.py (Settings field defaults)

它下面第五行是 `instance: get_or(cfg, "LIGHTHOUSE_INSTANCE_ID", "")`，
而 `backend/app/config.py` 对应那行是 `lighthouse_instance_id: str = "lhins-8clkew6k"`。
两边不一致，就在那句「mirror」正下方，躺了一个多月没人发现——因为「mirror」只是一句话，
没有任何东西比对过它。生产 .env 里恰好写了这个键，两个后端都读得到值，所以这一枚是哑弹；
下一枚不一定是。默认值分歧的形状恰恰是「平时看不出来」：只有在某个键**没被写进 .env**
的那台机器上（新机器、演练环境、回滚后那半小时）两套才会各跑各的。

这个脚本把那句注释变成一次比对：从 `main.dawn` 里机械抽出 `get_or(cfg, "KEY", "默认值")`
的表，从 `config.py` 的 `Settings` 里抽出字段默认值的表，逐键比。

**抽不出来就报错，不静默跳过。** 遇到自己解析不了的写法（换个函数、默认值是个表达式、
Dawn 字符串里带 `$` 插值）一律 exit 1 并点名到 file:line。一个「看不懂就当没有」的检查器，
和一个什么都不查的检查器，输出一模一样。

**豁免名单不是垃圾桶。** 只在一边有的键写在下面 `DAWN_ONLY` / `PY_ONLY` 里，每条都得写清
为什么。名单本身也被检查：某条豁免所指的键已经两边都有了、或者已经两边都没了，都会报错，
免得它变成下一个藏东西的地方。

    scripts/check_config_defaults.py              # 比对，有分歧就 exit 1
    scripts/check_config_defaults.py --self-test  # 负控：喂它假表，确认它真会红
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DAWN_SRC = REPO / "backend-dawn" / "src"
PY_CONFIG = REPO / "backend" / "app" / "config.py"

# config.dawn 是 get_or 的定义处，它自己的内联 test 里有 `get_or(c, "X", "def")`
# 这种示例调用——那不是配置读取点。除它以外的每个 .dawn 都扫，所以哪天读配置的代码
# 搬出 main.dawn，这里照样看得见。
DAWN_SKIP = {"config.dawn"}

# 只在 Dawn 侧存在的键。每条都必须说明为什么 FastAPI 那边没有对应物。
DAWN_ONLY = {
    "DAWN_PORT": "两个后端同机共存，必须各占一个端口（Dawn 8001 / uvicorn 8000）。"
    "FastAPI 的端口是 systemd 单元里 uvicorn 的命令行参数，根本不是 Settings 字段。",
    "DAWN_CORS_ORIGIN": "形状不同，无法逐字比：FastAPI 的 CORS_ORIGINS 是逗号分隔的"
    "多来源列表，Dawn 只收一个来源。",
    "DAWN_DB_PATH": "形状不同：FastAPI 的 DATABASE_URL 是 SQLAlchemy URL"
    "（`sqlite:///./dawnop.db`），Dawn 直接要文件路径。",
    "DAWN_SIMPLE_EXT": "形状不同：FastAPI 的 SIMPLE_EXTENSION_PATH 留空表示"
    "「用 backend/extensions/libsimple」，Dawn 把那个路径本身写成默认值。",
    "QINIU_RS_HOST": "Dawn 自己拼七牛管理 REST 的地址，FastAPI 用官方 SDK、域名由"
    "SDK 内部决定，没有对应设置。（契约假桶就是从这个键注入的）",
    "QINIU_UP_HOST": "同 QINIU_RS_HOST，上传域名那一半。",
}

# 只在 FastAPI 侧存在的字段。键名是字段名大写（pydantic-settings 的默认 env 名）。
PY_ONLY = {
    "APP_NAME": "只用于 FastAPI 的 OpenAPI 标题；Dawn 不出 /docs。",
    "ALGORITHM": "JWT 算法。Dawn 侧 HS256 是 util/jwt.dawn 里的常量 HEADER_JSON，"
    "不可配置，所以没有对应的键。",
    "DATABASE_URL": "见 DAWN_DB_PATH。",
    "SIMPLE_EXTENSION_PATH": "见 DAWN_SIMPLE_EXT。",
    "CORS_ORIGINS": "见 DAWN_CORS_ORIGIN。",
    "ROLLBACK_PROBE_HEADER": "回滚探针 GET /api/rollback/db-identity 只存在于 FastAPI——"
    "它就是回滚目标，探针是回滚脚本用来确认「现在连的是哪个库」的。",
    "ADMIN_USERNAME": "只有 backend/scripts/seed_admin.py 读它建管理员，Dawn 不建用户。",
    "ADMIN_PASSWORD": "同 ADMIN_USERNAME。",
}


class Unreadable(Exception):
    """源码里出现了这个脚本不敢猜的写法。宁可停下点名，也不要少读一个键。"""


# --------------------------------------------------------------------------
# Dawn 侧：get_or(cfg, "KEY", "默认值") 与 require_int_in_range(cfg, "KEY", lo, hi, 默认值)
# --------------------------------------------------------------------------

# 先找出每一处调用（宽），再要求它整体匹配严格形式（窄）。宽的那一步没有匹配到的
# 写法就是「解析不了」，会抛 Unreadable——而不是悄悄不算进表里。
_CALL = re.compile(r"\b(get_or|require_int_in_range)\s*\(")
_GET_OR = re.compile(
    r'get_or\(\s*\w+\s*,\s*"([A-Z][A-Z0-9_]*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)'
)
_INT_RANGE = re.compile(
    r'require_int_in_range\(\s*\w+\s*,\s*"([A-Z][A-Z0-9_]*)"\s*,'
    r"\s*-?\d+\s*,\s*-?\d+\s*,\s*(-?\d+)\s*\)"
)
_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "r": "\r"}


def _unescape(raw: str, where: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(raw):
        c = raw[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= len(raw) or raw[i + 1] not in _ESCAPES:
            raise Unreadable(f"{where}: 默认值里有看不懂的转义 {raw[i : i + 2]!r}")
        out.append(_ESCAPES[raw[i + 1]])
        i += 2
    return "".join(out)


def dawn_defaults(sources: dict[str, str]) -> dict[str, tuple[str, str]]:
    """{KEY: (默认值, "文件:行号")}。sources 是 {文件名: 源码}。"""
    found: dict[str, tuple[str, str]] = {}
    for name, text in sorted(sources.items()):
        for call in _CALL.finditer(text):
            where = f"{name}:{text.count(chr(10), 0, call.start()) + 1}"
            strict = _GET_OR.match(text, call.start()) or _INT_RANGE.match(
                text, call.start()
            )
            if strict is None:
                raise Unreadable(
                    f"{where}: 这处 {call.group(1)} 不是本脚本认得的形式，"
                    "无法抽出默认值。改写成 "
                    '`get_or(cfg, "KEY", "默认值")` 或教会这个脚本。'
                )
            key, raw = strict.group(1), strict.group(2)
            value = _unescape(raw, where) if strict.re is _GET_OR else raw
            if "$" in value:
                raise Unreadable(
                    f"{where}: 键 {key} 的默认值 {value!r} 里有 `$` 插值，"
                    "不是一个能与 Python 比对的字面量。"
                )
            if key in found and found[key][0] != value:
                raise Unreadable(
                    f"{where}: 键 {key} 在 Dawn 侧被读了两次且默认值不同"
                    f"（{found[key][1]} 读作 {found[key][0]!r}，这里 {value!r}）。"
                )
            found[key] = (value, where)
    return found


def dawn_sources() -> dict[str, str]:
    files = {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted(DAWN_SRC.rglob("*.dawn"))
        if p.name not in DAWN_SKIP
    }
    if not files:
        raise Unreadable(f"{DAWN_SRC} 下一个 .dawn 都没有，这条守卫在空跑")
    return files


# --------------------------------------------------------------------------
# FastAPI 侧：Settings 的字段默认值
# --------------------------------------------------------------------------


def python_defaults(
    source: str, filename: str = "config.py"
) -> dict[str, tuple[str, str]]:
    """{KEY: (默认值, "文件:行号")}。KEY 是字段名大写，即 pydantic 认的 env 名。"""
    tree = ast.parse(source, filename)
    cls = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "Settings"
        ),
        None,
    )
    if cls is None:
        raise Unreadable(f"{filename}: 找不到 class Settings")

    found: dict[str, tuple[str, str]] = {}
    for node in cls.body:
        where = f"{filename}:{node.lineno}"
        # 文档字符串、property / 方法：不是配置字段。
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        ):
            continue
        # pydantic 的模型配置，唯一允许的无注解赋值。
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if targets == ["model_config"]:
                continue
            raise Unreadable(f"{where}: Settings 里出现了看不懂的赋值 {targets}")
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            raise Unreadable(
                f"{where}: Settings 里出现了看不懂的语句 {type(node).__name__}"
            )

        name = node.target.id
        if node.value is None:
            raise Unreadable(
                f"{where}: 字段 {name} 没有默认值（必填设置）。"
                "Dawn 侧没有「必填」这个形状，需要先决定两边怎么对齐再教会这个脚本。"
            )
        if (
            not isinstance(node.value, ast.Constant)
            or not isinstance(node.value.value, (str, int))
            or isinstance(node.value.value, bool)
        ):
            raise Unreadable(
                f"{where}: 字段 {name} 的默认值不是字符串或整数字面量，无法与 .env 里的"
                "文本比对。"
            )
        found[name.upper()] = (str(node.value.value), where)
    return found


# --------------------------------------------------------------------------
# 比对
# --------------------------------------------------------------------------


def compare(
    dawn: dict[str, tuple[str, str]],
    py: dict[str, tuple[str, str]],
    dawn_only: dict[str, str],
    py_only: dict[str, str],
) -> list[str]:
    """返回问题清单，空列表 = 两边一致。

    四类问题，缺一类就有一整类漂移是隐形的：
      * 两边都有、默认值不同 → LIGHTHOUSE_INSTANCE_ID 走的就是这条；
      * 只在 Dawn 有、且没写进豁免 → 新键忘了同步给 FastAPI；
      * 只在 FastAPI 有、且没写进豁免 → 反方向；
      * 豁免过期（那个键现在两边都有 / 两边都没有）→ 名单在替代码撒谎。
    """
    problems: list[str] = []

    for key in sorted(set(dawn) & set(py)):
        if dawn[key][0] != py[key][0]:
            problems.append(
                f"{key}: 默认值不同（Dawn {dawn[key][0]!r} @ {dawn[key][1]}，"
                f"FastAPI {py[key][0]!r} @ {py[key][1]}）"
            )
    for key in sorted(set(dawn) - set(py)):
        if key not in dawn_only:
            problems.append(
                f"{key}: 只有 Dawn 读（{dawn[key][1]}），FastAPI 侧没有对应字段。"
                "补上，或写进 DAWN_ONLY 并说明理由。"
            )
    for key in sorted(set(py) - set(dawn)):
        if key not in py_only:
            problems.append(
                f"{key}: 只有 FastAPI 有（{py[key][1]}），Dawn 侧没有读这个键。"
                "补上，或写进 PY_ONLY 并说明理由。"
            )

    for key in sorted(dawn_only):
        if key in py:
            problems.append(f"{key}: DAWN_ONLY 说它只在 Dawn 侧，但 FastAPI 现在也有了")
        elif key not in dawn:
            problems.append(f"{key}: DAWN_ONLY 里有它，但 Dawn 侧已经不读这个键了")
    for key in sorted(py_only):
        if key in dawn:
            problems.append(f"{key}: PY_ONLY 说它只在 FastAPI 侧，但 Dawn 现在也读它了")
        elif key not in py:
            problems.append(f"{key}: PY_ONLY 里有它，但 FastAPI 已经没有这个字段了")

    return problems


# --------------------------------------------------------------------------
# 负控：喂假表，确认每条判词都真会红
# --------------------------------------------------------------------------


def self_test() -> int:
    def d(**kv):
        return {k: (v, "main.dawn:1") for k, v in kv.items()}

    def p(**kv):
        return {k: (v, "config.py:1") for k, v in kv.items()}

    # 一致就必须是空列表。少了这条，compare 可以写成「无脑抱怨一句」而其余全绿。
    assert compare(d(A="1"), p(A="1"), {}, {}) == []

    # 真实那次分歧的形状。
    got = compare(
        d(LIGHTHOUSE_INSTANCE_ID=""), p(LIGHTHOUSE_INSTANCE_ID="lhins-x"), {}, {}
    )
    assert len(got) == 1 and "默认值不同" in got[0], got

    # 数字与字符串按文本比：Settings 的 1440 与 .env 里的 "1440" 是同一件事。
    assert compare(d(N="1440"), p(N="1440"), {}, {}) == []
    assert len(compare(d(N="1440"), p(N="60"), {}, {})) == 1

    # 单边键：没豁免就报，有豁免就闭嘴。
    assert len(compare(d(ONLY_DAWN="x"), p(), {}, {})) == 1
    assert compare(d(ONLY_DAWN="x"), p(), {"ONLY_DAWN": "理由"}, {}) == []
    assert len(compare(d(), p(ONLY_PY="x"), {}, {})) == 1
    assert compare(d(), p(ONLY_PY="x"), {}, {"ONLY_PY": "理由"}) == []

    # 过期豁免：键回到两边、或从两边消失，名单都得跟着改。
    assert len(compare(d(A="1"), p(A="1"), {"A": "理由"}, {})) == 1
    assert len(compare(d(), p(), {"GONE": "理由"}, {})) == 1
    assert len(compare(d(), p(), {}, {"GONE": "理由"})) == 1

    # 抽取：认得的写法要抽对。
    src = {
        "main.dawn": (
            'let a = get_or(cfg, "SECRET_KEY", "dev-insecure")\n'
            'let b = get_or(cfg, "EMPTY", "")\n'
            'let c = require_int_in_range(cfg, "QINIU_TOKEN_EXPIRES", 1, 86400, 3600)\n'
            'let d = get_or(cfg, "ESCAPED", "a\\"b")\n'
        )
    }
    table = dawn_defaults(src)
    assert table["SECRET_KEY"][0] == "dev-insecure"
    assert table["EMPTY"][0] == ""
    assert table["QINIU_TOKEN_EXPIRES"][0] == "3600"
    assert table["ESCAPED"][0] == 'a"b'
    assert (
        table["SECRET_KEY"][1] == "main.dawn:1" and table["ESCAPED"][1] == "main.dawn:4"
    )

    # 抽取：认不得的写法要**炸**，不是当作没有。这是这个脚本最容易变绿的地方。
    for bad in (
        'get_or(cfg, key_var, "x")',  # 键不是字面量
        'get_or(cfg, "K", fallback)',  # 默认值不是字面量
        'get_or(cfg, "K", "" ++ suffix)',  # 默认值是表达式
        'get_or(cfg, "K", "$port")',  # Dawn 字符串插值
        'require_int_in_range(cfg, "K", 1, 86400, dflt)',
        'let x = get_or(cfg, "K", "a")\nlet y = get_or(cfg, "K", "b")',  # 同键两个默认值
    ):
        try:
            dawn_defaults({"t.dawn": bad})
        except Unreadable:
            pass
        else:
            raise AssertionError(f"这处应当被拒绝却没有：{bad!r}")

    ok = "class Settings(BaseSettings):\n    model_config = 1\n    a: str = 'x'\n    b: int = 2\n"
    assert python_defaults(ok) == {"A": ("x", "config.py:3"), "B": ("2", "config.py:4")}
    for bad in (
        "class Settings(BaseSettings):\n    a: str = os.environ['X']\n",  # 算出来的默认值
        "class Settings(BaseSettings):\n    a: str\n",  # 必填字段
        "class Settings(BaseSettings):\n    a: bool = True\n",  # 布尔与 .env 文本的映射未定义
        "class Nope(BaseSettings):\n    a: str = 'x'\n",  # 类改名了
        "class Settings(BaseSettings):\n    x = 'y'\n",  # 无注解赋值
    ):
        try:
            python_defaults(bad)
        except Unreadable:
            pass
        else:
            raise AssertionError(f"这处应当被拒绝却没有：{bad!r}")

    print("self-test: 每条判词都能转红。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true", help="只跑负控，不看真实的树")
    args = ap.parse_args()
    if args.self_test:
        return self_test()

    try:
        dawn = dawn_defaults(dawn_sources())
        py = python_defaults(PY_CONFIG.read_text(encoding="utf-8"), "app/config.py")
    except Unreadable as e:
        print(f"读不懂：{e}")
        return 1

    problems = compare(dawn, py, DAWN_ONLY, PY_ONLY)
    shared = sorted(set(dawn) & set(py))

    # 豁免逐条打出来，每次都打。否则「CI 跑过了」会悄悄变成「CI 什么都没比」。
    for key in sorted(DAWN_ONLY):
        print(f"SKIP  {key:<24} 只在 Dawn 侧：{DAWN_ONLY[key]}")
    for key in sorted(PY_ONLY):
        print(f"SKIP  {key:<24} 只在 FastAPI 侧：{PY_ONLY[key]}")

    if problems:
        for p in problems:
            print(f"DRIFT {p}")
        print(
            f"\n共 {len(problems)} 处分歧。两个后端读同一个 .env，默认值必须是同一套。"
        )
        return 1

    for key in shared:
        print(f"OK    {key:<24} {dawn[key][0]!r}")
    print(
        f"\n{len(shared)} 个共享键的默认值两边一致（另有 {len(DAWN_ONLY) + len(PY_ONLY)} 个具名豁免）。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
