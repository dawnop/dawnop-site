#!/usr/bin/env python3
"""两个后端读同一个 .env，那它们对「没写这个键」的理解也必须是同一个。

**为什么存在。** `backend-dawn/src/main.dawn` 里有一句注释：

    # defaults mirror app/config.py (Settings field defaults)

它下面第五行是 `instance: get_or(cfg, "LIGHTHOUSE_INSTANCE_ID", "")`，
而 `backend/app/config.py` 对应那行是 `lighthouse_instance_id: str = "lhins-xxxxxxxx"`
（真实的实例 ID 已抹去，它是这台机器的身份，不该躺在公开仓库里）。
两边不一致，就在那句「mirror」正下方，躺了一个多月没人发现——因为「mirror」只是一句话，
没有任何东西比对过它。生产 .env 里恰好写了这个键，两个后端都读得到值，所以这一枚是哑弹；
下一枚不一定是。默认值分歧的形状恰恰是「平时看不出来」：只有在某个键**没被写进 .env**
的那台机器上（新机器、演练环境、回滚后那半小时）两套才会各跑各的。

这个脚本把那句注释变成一次比对：从 `main.dawn` 里机械抽出 `get_or(cfg, "KEY", "默认值")`
的表，从 `config.py` 的 `Settings` 里抽出字段默认值的表，逐键比。

**第三张表是模板。** `backend/.env.example` 是人唯一会照抄的那份，可它既不是代码也没被
任何东西检查过：模板缺一个键，操作员就不知道有这个开关；模板多一个没人读的键，改了它
不会有任何效果，而人会以为改了；模板里的值和代码默认值不同，那「不写这一行」和
「照抄这一行」就是两种行为。三张表逐键对齐，这三种谎话才都说不出口。

模板还有一条只有它才有的约束：两个后端用**两个不同的解析器**读同一个 .env。
`backend-dawn/src/config.dawn` 的 `parse_env` 会 trim 键值、剥成对引号，但不处理行内注释、
不认 `export`、不认 `${VAR}` 插值；python-dotenv（pydantic-settings 用的那个）四样都做。
于是 `SECRET_KEY=abc#def` 在 FastAPI 读作 `abc`、在 Dawn 读作 `abc#def`。模板是给人抄的，
所以它必须整份落在两个解析器读法相同的子集里，越界就报 Unreadable 并点名到行号。

**抽不出来就报错，不静默跳过。** 遇到自己解析不了的写法（换个函数、默认值是个表达式、
Dawn 字符串里带 `$` 插值）一律 exit 1 并点名到 file:line。一个「看不懂就当没有」的检查器，
和一个什么都不查的检查器，输出一模一样。

**豁免名单不是垃圾桶。** 只在一边有的键写在下面 `DAWN_ONLY` / `PY_ONLY` 里，模板不提供的
键写在 `TEMPLATE_OMIT` 里，模板值故意不等于代码默认值的写在 `TEMPLATE_DIFFERS` 里，每条
都得写清为什么。四张名单本身也被检查：某条豁免所指的情形已经不成立了就报错，免得它变成
下一个藏东西的地方。「模板里有个没人读的键」这一条**没有豁免名单**，模板里一个没人读的
键就是谎话，没有说得通的版本。

    scripts/check_config_defaults.py              # 比对，有分歧就 exit 1
    scripts/check_config_defaults.py --self-test  # 负控：喂它假表，确认它真会红
    scripts/check_config_defaults.py --mutants    # 负控：改真实输入，确认每条判词真会红
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DAWN_SRC = REPO / "backend-dawn" / "src"
PY_CONFIG = REPO / "backend" / "app" / "config.py"
TEMPLATE = REPO / "backend" / ".env.example"

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


# 后端读、但**故意不写进模板**的键。模板是一份邀请：写进去的每一行都在说「这个你可以改」。
# 所以这张名单的每条理由都得答同一个问题——为什么不该邀请人去改它。
TEMPLATE_OMIT = {
    "ALGORITHM": "Dawn 侧 HS256 是 util/jwt.dawn 里的常量，写死的。在 .env 里改这个键"
    "只动 FastAPI 一半，结果是两个后端签发的 token 互不认。模板不提供它，"
    "就是不邀请人去改。",
    "QINIU_RS_HOST": "它存在只是为了让契约假桶"
    "（backend-dawn/scripts/contract_qiniu_fake.py）把 Dawn 指到 127.0.0.1。"
    "真实部署永远不该设，写进模板等于邀请人去设。",
    "QINIU_UP_HOST": "同 QINIU_RS_HOST，上传域名那一半。",
}

# 模板里的值**故意**不等于代码默认值的键。默认情况下两者必须逐字相等，否则
# 「不写这一行」与「照抄这一行」是两种行为，而模板不会告诉你是哪两种。
TEMPLATE_DIFFERS = {
    "SECRET_KEY": "故意不同，两个值是给两个不同的人看的：模板里那句是对准备部署的人喊"
    "「换掉我」，代码默认值是本地开发跑得起来的兜底。两者相等反而糟糕，"
    "那样模板就成了一个可用的生产密钥。",
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
# 模板侧：backend/.env.example
# --------------------------------------------------------------------------
#
# 这里不实现「.env 语法」，只实现两个解析器**读法相同**的那个子集。凡是两边会读出
# 不同结果的写法，一律拒绝：模板是给人抄的，抄一行出来两个值是最难查的那种分歧，
# 因为两个后端都不会报错，只是各跑各的。
#
# 逐条对应到实测的分歧：
#   `export K=v`     python-dotenv 剥掉 export，Dawn 的键会变成 "export K"
#   值里有 `#`       python-dotenv 当行内注释截断，Dawn 原样保留
#   值里有 `$`       python-dotenv 做 ${VAR} 插值，Dawn 原样保留
#   值里有 `\`       双引号里 python-dotenv 认 \n 等转义，Dawn 不认
#   值被引号包起来   两边都剥，但只有引号内的 # $ \ 才走上面三条，形状太容易看错，不许用
#   `=` 两侧有空白   Dawn trim，python-dotenv 也 trim 键、但值的处理与引号纠缠
# 剩下的三条（重复键、没有 `=`、键名不是 A-Z 形状）不是分歧而是笔误，一样拒绝：
# 一份模板里没有哪一条值得靠猜。

_TEMPLATE_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")


def template_defaults(
    text: str, filename: str = ".env.example"
) -> dict[str, tuple[str, str]]:
    """{KEY: (值, "文件:行号")}。合法行只有三种：空行、`#` 注释、`KEY=value`。"""
    found: dict[str, tuple[str, str]] = {}
    for lineno, line in enumerate(text.split("\n"), start=1):
        where = f"{filename}:{lineno}"
        if line == "":
            continue
        if line != line.strip():
            raise Unreadable(f"{where}: 行首或行尾有空白，去掉它。")
        if line.startswith("#"):
            continue
        if line.startswith("export "):
            raise Unreadable(
                f"{where}: `export ` 前缀。python-dotenv 会剥掉它，config.dawn 不会"
                "（Dawn 那边的键会变成 `export ...`），两个后端读出两件事。"
            )
        if "=" not in line:
            raise Unreadable(
                f"{where}: 既不是空行也不是注释，却没有 `=`：{line!r}。"
                "模板里的每一行都得是 `KEY=value`。"
            )
        key, value = line.split("=", 1)
        if key != key.strip() or value != value.strip():
            raise Unreadable(
                f"{where}: `=` 两侧有空白。两个解析器对空白的处理与引号纠缠在一起，"
                "写成 `KEY=value` 就没这个问题。"
            )
        if not _TEMPLATE_KEY.match(key):
            raise Unreadable(
                f"{where}: 键名 {key!r} 不匹配 ^[A-Z][A-Z0-9_]*$。"
                "两个后端都按这个形状找键。"
            )
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            raise Unreadable(
                f"{where}: 键 {key} 的值被引号包起来了。两边都会剥这对引号，但引号内的"
                "转义与插值两边不一样，形状太容易看错。去掉引号直接写值。"
            )
        for bad, why in (
            ("#", "python-dotenv 会把它当行内注释、就地截断，Dawn 原样保留"),
            ("$", "python-dotenv 会做 ${VAR} 插值，Dawn 原样保留"),
            ("\\", "双引号里 python-dotenv 认 \\n 这类转义，Dawn 不认"),
        ):
            if bad in value:
                raise Unreadable(
                    f"{where}: 键 {key} 的值里有 {bad!r}：{why}。"
                    "同一行两个后端读出两个值。"
                )
        if key in found:
            raise Unreadable(
                f"{where}: 键 {key} 出现了两次（上一处在 {found[key][1]}）。"
                "两个解析器都取最后一个，但读模板的人取的是先看见的那个。"
            )
        found[key] = (value, where)
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


def compare_template(
    template: dict[str, tuple[str, str]],
    dawn: dict[str, tuple[str, str]],
    py: dict[str, tuple[str, str]],
    template_omit: dict[str, str],
    template_differs: dict[str, str],
) -> list[str]:
    """模板与两个后端的比对。返回问题清单，空列表 = 一致。

    四类问题：
      * 后端读它、模板里没有 → 操作员不知道有这个开关（无 DAWN_PORT 那三个键就是这样）；
      * 模板里有、没人读 → 改了它不会有任何效果，而模板让人以为会；
      * 两边都有、值不同 → 「不写这一行」与「照抄这一行」是两种行为；
      * 豁免过期 → 名单在替模板撒谎。
    """
    problems: list[str] = []

    # 两边都读的键，取哪份都行：compare() 已经保证它们相等，不等的话那边先红。
    read = {**py, **dawn}

    for key in sorted(read):
        if key not in template and key not in template_omit:
            problems.append(
                f"{key}: 后端读它（{read[key][1]}），模板里没有它。"
                "补进 backend/.env.example，或写进 TEMPLATE_OMIT 并说明理由。"
            )
    # 这一条没有豁免名单，也不该有：模板里一个没人读的键就是谎话。
    for key in sorted(template):
        if key not in read:
            problems.append(
                f"{key}: 模板里有它（{template[key][1]}），但两个后端都没有读这个键。"
                "删掉它，或补上读它的代码。"
            )
    for key in sorted(set(template) & set(read)):
        if template[key][0] != read[key][0] and key not in template_differs:
            problems.append(
                f"{key}: 模板里的值 {template[key][0]!r}（{template[key][1]}）"
                f"与代码默认值 {read[key][0]!r}（{read[key][1]}）不同。"
                "改成一致，或写进 TEMPLATE_DIFFERS 并说明理由。"
            )

    for key in sorted(template_omit):
        if key in template:
            problems.append(
                f"{key}: TEMPLATE_OMIT 说模板不该提供它，"
                f"但模板现在有了（{template[key][1]}）"
            )
        elif key not in read:
            problems.append(f"{key}: TEMPLATE_OMIT 里有它，但已经没有后端读这个键了")
    for key in sorted(template_differs):
        if key not in template:
            problems.append(f"{key}: TEMPLATE_DIFFERS 里有它，但模板里已经没有这个键了")
        elif key in read and template[key][0] == read[key][0]:
            problems.append(
                f"{key}: TEMPLATE_DIFFERS 说它与代码默认值不同，但两者现在相等"
            )

    return problems


def check_all(
    dawn_sources_text: dict[str, str], py_text: str, template_text: str
) -> list[str]:
    """三份文本进，问题清单出。纯函数，不碰文件系统——`--mutants` 靠它在内存里改输入。"""
    dawn = dawn_defaults(dawn_sources_text)
    py = python_defaults(py_text, "app/config.py")
    template = template_defaults(template_text)
    return compare(dawn, py, DAWN_ONLY, PY_ONLY) + compare_template(
        template, dawn, py, TEMPLATE_OMIT, TEMPLATE_DIFFERS
    )


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

    # 模板抽取：三种合法行都要读对，行号要跟得上。
    tpl = template_defaults("# 注释\nA=1\n\nB=\nC=a b c\nD=sqlite:///./x.db\n", "t.env")
    assert tpl["A"] == ("1", "t.env:2")
    assert tpl["B"] == ("", "t.env:4")
    assert tpl["C"] == ("a b c", "t.env:5")
    assert tpl["D"] == ("sqlite:///./x.db", "t.env:6")

    # 模板抽取：越出「两个解析器读法相同」的子集就得炸，一条都不许静默通过。
    for bad in (
        "export A=1\n",  # python-dotenv 剥 export，Dawn 不剥
        "A=a#b\n",  # python-dotenv 当行内注释截断
        "A=${B}\n",  # python-dotenv 插值
        "A=a\\nb\n",  # 转义
        'A="1"\n',  # 引号
        "A='1'\n",
        "A = 1\n",  # `=` 两侧空白
        "A=1 \n",  # 行尾空白
        " A=1\n",  # 行首空白
        "A\n",  # 非空非注释又没有 `=`
        "a=1\n",  # 键名形状
        "1A=1\n",
        "A=1\nA=2\n",  # 重复键
    ):
        try:
            template_defaults(bad, "t.env")
        except Unreadable:
            pass
        else:
            raise AssertionError(f"这行模板应当被拒绝却没有：{bad!r}")

    # 模板比对。t() 造模板表，d()/p() 复用上面的。
    def t(**kv):
        return {k: (v, "t.env:1") for k, v in kv.items()}

    # 阳性对照：三张表一致就必须是空列表。
    assert compare_template(t(A="1"), d(A="1"), p(A="1"), {}, {}) == []
    assert compare_template(t(A="1"), d(A="1"), p(), {}, {}) == []
    assert compare_template(t(A="1"), d(), p(A="1"), {}, {}) == []

    # 覆盖：后端读、模板没有。有豁免才闭嘴。
    got = compare_template(t(), d(A="1"), p(), {}, {})
    assert len(got) == 1 and "模板里没有它" in got[0], got
    assert compare_template(t(), d(A="1"), p(), {"A": "理由"}, {}) == []

    # 无孤儿：模板有、没人读。**没有**豁免名单可用。
    got = compare_template(t(A="1"), d(), p(), {}, {})
    assert len(got) == 1 and "两个后端都没有读这个键" in got[0], got

    # 值一致：不同就报，除非具名。
    got = compare_template(t(A="1"), d(A="2"), p(), {}, {})
    assert len(got) == 1 and "改成一致，或写进 TEMPLATE_DIFFERS" in got[0], got
    assert compare_template(t(A="1"), d(A="2"), p(), {}, {"A": "理由"}) == []

    # 过期豁免：TEMPLATE_OMIT 的键回到模板里了、或者已经没人读了。
    got = compare_template(t(A="1"), d(A="1"), p(), {"A": "理由"}, {})
    assert len(got) == 1 and "TEMPLATE_OMIT 说模板不该提供它" in got[0], got
    got = compare_template(t(), d(), p(), {"A": "理由"}, {})
    assert len(got) == 1 and "已经没有后端读这个键了" in got[0], got

    # 过期豁免：TEMPLATE_DIFFERS 的键值已经相等了、或者已经不在模板里了。
    got = compare_template(t(A="1"), d(A="1"), p(), {}, {"A": "理由"})
    assert len(got) == 1 and "但两者现在相等" in got[0], got
    got = compare_template(t(), d(A="1"), p(), {"A": "理由"}, {"A": "理由"})
    assert any("模板里已经没有这个键了" in g for g in got), got

    print("self-test: 每条判词都能转红。")
    return 0


# --------------------------------------------------------------------------
# 负控之二：改真实输入，确认每条判词在真实代码路径下也真会红
# --------------------------------------------------------------------------
#
# self-test 喂的是合成表，证明的是判词函数本身会红；这里喂的是仓库里真正的三份文本，
# 证明的是「抽取 → 比对」整条路在真实形状上也会红。两者不能互相替代：合成表全绿的
# 检查器可能连真实文件都没打开，而只跑真实文件的检查器分不清「没抓到」和「没有」。
#
# **一个字节都不写进文件系统。** 读文本、在内存里改字符串、跑纯函数。#269 的教训是
# 一个变异脚本被指向活检出，就地改写了生产源码；这个脚本没有那种可能，因为它没有写。
#
# 每处变异都**锚定**：`text.count(old) == 1`，否则抛异常。锚点失配要炸——一个悄悄变成
# 空操作的变异，和一个通过了的变异，输出一模一样。


def _mutate(text: str, old: str, new: str) -> str:
    n = text.count(old)
    if n != 1:
        raise AssertionError(f"锚点在文本里出现 {n} 次，要求恰好 1 次：{old!r}")
    return text.replace(old, new)


def mutants() -> int:
    srcs = dawn_sources()
    py_text = PY_CONFIG.read_text(encoding="utf-8")
    tpl_text = TEMPLATE.read_text(encoding="utf-8")

    def expect(problems: list[str], *needles: str) -> None:
        hit = [p for p in problems if all(n in p for n in needles)]
        if not hit:
            raise AssertionError(
                f"期望的判词没出现（要含 {needles}），实际：{problems}"
            )

    def expect_unreadable(fn, *needles: str) -> None:
        try:
            fn()
        except Unreadable as e:
            for n in needles:
                if n not in str(e):
                    raise AssertionError(f"Unreadable 消息里没有 {n!r}：{e}") from None
        else:
            raise AssertionError(f"期望 Unreadable（含 {needles}），却通过了")

    def with_template(old: str, new: str) -> list[str]:
        return check_all(srcs, py_text, _mutate(tpl_text, old, new))

    # 8. 阳性对照放在最前：真实的三份输入必须是绿的。少了这条，下面七条全红也
    #    可能只是因为这个检查器对任何输入都报错。
    clean = check_all(srcs, py_text, tpl_text)
    if clean != []:
        raise AssertionError(f"未变异的真实输入应当是绿的，却报了：{clean}")

    # 1. 模板少一个后端确实会读的键。
    expect(with_template("DAWN_PORT=8001\n", ""), "模板里没有它", "DAWN_PORT")

    # 2. 模板多一个没人读的键。
    expect(
        with_template("ADMIN_PASSWORD=change-me", "ADMIN_PASSWORD=change-me\nNOPE=1"),
        "两个后端都没有读这个键",
        "NOPE",
    )

    # 3. 模板的值与代码默认值分家。
    expect(
        with_template("QINIU_TOKEN_EXPIRES=3600", "QINIU_TOKEN_EXPIRES=3601"),
        "改成一致，或写进 TEMPLATE_DIFFERS",
        "QINIU_TOKEN_EXPIRES",
    )

    # 4. TEMPLATE_OMIT 里的键回到了模板里，豁免过期。
    expect(
        with_template(
            "ACCESS_TOKEN_EXPIRE_MINUTES=1440",
            "ACCESS_TOKEN_EXPIRE_MINUTES=1440\nALGORITHM=HS256",
        ),
        "TEMPLATE_OMIT 说模板不该提供它",
        "ALGORITHM",
    )

    # 5. 模板越出两个解析器的公共子集：export 前缀。行号要点到那一行。
    lineno = tpl_text.split("\n").index("APP_NAME=dawnop-site") + 1
    expect_unreadable(
        lambda: with_template("APP_NAME=dawnop-site", "export APP_NAME=dawnop-site"),
        f".env.example:{lineno}",
        "export",
    )

    # 6. 同上：值里的 `#`，FastAPI 读作 `a`、Dawn 读作 `a#b` 的那一枚。
    expect_unreadable(
        lambda: with_template(
            "SECRET_KEY=change-me-to-a-long-random-string", "SECRET_KEY=a#b"
        ),
        "SECRET_KEY",
        "'#'",
    )

    # 7. 不是模板的判词：把 Dawn 侧读 LIGHTHOUSE_INSTANCE_ID 那一行删掉，#272 那批规则
    #    必须照样红。新加的三张表不能把老判词挤下去。
    mutated = dict(srcs)
    mutated["main.dawn"] = _mutate(
        srcs["main.dawn"],
        '    instance: get_or(cfg, "LIGHTHOUSE_INSTANCE_ID", ""),\n',
        "",
    )
    expect(
        check_all(mutated, py_text, tpl_text),
        "只有 FastAPI 有",
        "LIGHTHOUSE_INSTANCE_ID",
    )

    print("mutants: 7 个变异全部按预期转红，干净输入为绿。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true", help="只跑负控，不看真实的树")
    ap.add_argument(
        "--mutants",
        action="store_true",
        help="负控之二：在内存里改真实的三份输入，确认每条判词都会红",
    )
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.mutants:
        return mutants()

    try:
        dawn = dawn_defaults(dawn_sources())
        py = python_defaults(PY_CONFIG.read_text(encoding="utf-8"), "app/config.py")
        template = template_defaults(TEMPLATE.read_text(encoding="utf-8"))
    except Unreadable as e:
        print(f"读不懂：{e}")
        return 1

    problems = compare(dawn, py, DAWN_ONLY, PY_ONLY) + compare_template(
        template, dawn, py, TEMPLATE_OMIT, TEMPLATE_DIFFERS
    )
    shared = sorted(set(dawn) & set(py))

    # 豁免逐条打出来，每次都打。否则「CI 跑过了」会悄悄变成「CI 什么都没比」。
    for key in sorted(DAWN_ONLY):
        print(f"SKIP  {key:<24} 只在 Dawn 侧：{DAWN_ONLY[key]}")
    for key in sorted(PY_ONLY):
        print(f"SKIP  {key:<24} 只在 FastAPI 侧：{PY_ONLY[key]}")
    for key in sorted(TEMPLATE_OMIT):
        print(f"SKIP  {key:<24} 不进模板：{TEMPLATE_OMIT[key]}")
    for key in sorted(TEMPLATE_DIFFERS):
        print(f"SKIP  {key:<24} 模板值与代码默认值具名不同：{TEMPLATE_DIFFERS[key]}")

    if problems:
        for p in problems:
            print(f"DRIFT {p}")
        print(
            f"\n共 {len(problems)} 处分歧。两个后端读同一个 .env，"
            "默认值必须是同一套，模板必须说的是同一件事。"
        )
        return 1

    for key in shared:
        print(f"OK    {key:<24} {dawn[key][0]!r}")
    print(
        f"\n{len(shared)} 个共享键的默认值两边一致（另有 {len(DAWN_ONLY) + len(PY_ONLY)} 个具名豁免）。"
    )
    print(
        f"{len(template)} 个模板键都有后端读，值与代码默认值一致"
        f"（另有 {len(TEMPLATE_OMIT)} 个不进模板、{len(TEMPLATE_DIFFERS)} 个具名不同）。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
