"""下载 wangfenjin/simple 中文分词扩展到 backend/extensions/。

用法（在 backend/ 下）：
    python scripts/fetch_simple_ext.py

按当前系统/架构选对应的预编译库，只取共享库本体（不含 jieba 词典——本项目仅用
simple_query，字级 + 拼音，不启用 jieba）。生产（Ubuntu 22.04 x86-64）上直接跑本脚本即可。
"""
import io
import platform
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSION = "v0.7.1"
BASE = f"https://github.com/wangfenjin/simple/releases/download/{VERSION}"

# (system, machine 前缀) -> 发布包名
_ASSETS = {
    ("Linux", "x86_64"): "libsimple-linux-ubuntu-22.04.zip",
    ("Linux", "aarch64"): "libsimple-linux-ubuntu-24.04-arm.zip",
    ("Linux", "arm64"): "libsimple-linux-ubuntu-24.04-arm.zip",
    ("Darwin", "arm64"): "libsimple-osx-arm64.zip",
    ("Darwin", "x86_64"): "libsimple-osx-x64.zip",
    ("Windows", "amd64"): "libsimple-windows-x64.zip",
    ("Windows", "x86_64"): "libsimple-windows-x64.zip",
}

DEST = Path(__file__).resolve().parent.parent / "extensions"


def main() -> None:
    system = platform.system()
    machine = platform.machine().lower()
    asset = _ASSETS.get((system, machine))
    if asset is None:
        print(f"未找到匹配的预编译包：{system}/{machine}。", file=sys.stderr)
        print("请到 https://github.com/wangfenjin/simple/releases 手动下载，", file=sys.stderr)
        print(f"把 libsimple.(so|dylib|dll) 放到 {DEST}/。", file=sys.stderr)
        sys.exit(1)

    url = f"{BASE}/{asset}"
    print(f"下载 {url} …")
    with urllib.request.urlopen(url) as resp:  # noqa: S310  官方发布地址
        blob = resp.read()

    DEST.mkdir(parents=True, exist_ok=True)
    extracted = None
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for name in zf.namelist():
            base = name.rsplit("/", 1)[-1]
            if base.startswith("libsimple.") and base.rsplit(".", 1)[-1] in {
                "so",
                "dylib",
                "dll",
            }:
                target = DEST / base
                target.write_bytes(zf.read(name))
                extracted = target
                break

    if extracted is None:
        print("压缩包里没找到 libsimple 共享库。", file=sys.stderr)
        sys.exit(1)
    print(f"已写入 {extracted}")
    print("后端启动会自动加载它；搜索将使用 simple 分词（字级 + 拼音）。")


if __name__ == "__main__":
    main()
