#!/usr/bin/env python3
"""守卫：本站服务器的身份信息不得进入这个公开仓库。

约定是「真实 IP / ssh 用户名只存本地私有记录，仓库里一律写 <user>@<server>」。
2026-06-23 手工脱敏过一次，11 天后一份部署文档又把真实 IP 和 `ssh <user>@…` 写了回去，
在公开 main 上挂了两周——当时只有约定，没有守卫。这个脚本就是那个守卫。

三条检查，性质不同：

* **ssh 用户名**（真·不公开）：ssh/scp/rsync 命令里的 user@host。这是唯一一条 DNS 查不到、
  只存在于文档里的信息。无网络依赖，永远运行。
* **服务器 IP**（其实不算秘密）：`dig dawnop.com` 人人可得，A 记录就是它。留这条纯粹是维持
  约定一致，不是保密措施——别把它当安全边界。做法是**现场解析本站域名拿到 IP 再去比对**，
  而不是把 IP 写进脚本：把要防的东西写进防守代码里等于没防，而且解析式还能自动跟着换机走。
* **旁路服务的身份**（2026-07-18 加）：443 上除本站外还共用着一个与本站无关的旁路服务，
  它的主机名/组件名/端口/用途属于 ssh 用户名那一类——只该在本地私有记录里，整套相关配置在
  ~/workspace/dawnop-ops/（私有，不推送）。这些是固定字面量（不像 IP 能 DNS 解析）。
  **匹配模式用 base64 存**（下面 `_ENCODED_TERMS`）：否则这个公开文件自身就把要防的字面量写成
  明文了，GitHub 搜索/抓取直接从守卫里就能读到，前功尽弃。运行时解码成正则。

早先的版本按「任何公网 IP 都可疑」来判，结果把 sqlite-jdbc 的版本号 3.36.0.3 和 DNSPod 的
公共解析器 119.29.29.29 当成了泄露。可疑的从来不是「公网 IP」这个形状，是「**我们的**服务器」。
"""

import base64
import ipaddress
import re
import socket
import subprocess
import sys

# 本站自有域名。解析结果 = 需要在仓库里保持不出现的地址。
OWN_DOMAINS = ["dawnop.com", "www.dawnop.com", "dav.dawnop.com", "vault.dawnop.com"]

IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

# ssh/scp/rsync 里的 user@host。
USER_AT_HOST = re.compile(
    r"\b(?:ssh|scp|rsync)\b[^\n]*?\b([a-z_][a-z0-9_-]{0,31})@([A-Za-z0-9.<>-]+)"
)

# 占位符，以及文档里合法出现的通用账户名。
ALLOWED_USERS = {"user", "root", "git"}

# 旁路服务的固定身份模式，base64 存（见 docstring：避免这个公开文件自身写成明文）。
# 任一出现即视为泄露——细节只在本地私有记录 ~/workspace/dawnop-ops/。
# 每项：(base64(正则源), 是否 IGNORECASE)。要加新词：base64.b64encode(pat.encode())。
_ENCODED_TERMS = [
    (b"cHJveHlcLmRhd25vcFwuY29t", False),
    (b"XGJnb3N0XGI=", True),
    (b"XGJtaWhvbW9cYg==", True),
    (b"XGIxODQ0M1xi", False),
    (b"56m/5aKZfOe/u+WimQ==", False),
    (b"c3NsX3ByZXJlYWQ=", False),
]
FORBIDDEN_TERMS = [
    re.compile(base64.b64decode(b).decode(), re.IGNORECASE if ic else 0)
    for b, ic in _ENCODED_TERMS
]

TEXT_SUFFIXES = (
    ".md",
    ".txt",
    ".sh",
    ".py",
    ".conf",
    ".service",
    ".yml",
    ".yaml",
    ".js",
    ".vue",
    ".json",
    ".toml",
    ".example",
    ".dawn",
    ".html",
    ".css",
)

SELF = "check-no-server-identity"


def own_ips() -> set[str]:
    """解析本站域名。解析不了就报错退出——静默跳过等于把这条检查悄悄关掉。"""
    found: set[str] = set()
    errors: list[str] = []
    for d in OWN_DOMAINS:
        try:
            for info in socket.getaddrinfo(d, None, socket.AF_INET):
                ip = info[4][0]
                if not ipaddress.IPv4Address(ip).is_private:
                    found.add(ip)
        except OSError as e:
            errors.append(f"{d}: {e}")
    if not found:
        print(
            "错误：本站域名一个都没解析出来，IP 检查无法进行：\n  "
            + "\n  ".join(errors),
            file=sys.stderr,
        )
        sys.exit(2)
    return found


def tracked_text_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, text=True, check=True
    ).stdout
    return [f for f in out.split("\0") if f and f.endswith(TEXT_SUFFIXES)]


def main() -> int:
    ips = own_ips()
    hits: list[str] = []

    for path in tracked_text_files():
        if SELF in path:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except (OSError, UnicodeDecodeError):
            continue

        for n, line in enumerate(lines, 1):
            if SELF in line:
                continue
            for m in IPV4.finditer(line):
                if m.group(0) in ips:
                    hits.append(
                        f"{path}:{n}: 本站服务器 IP `{m.group(0)}` —— 改成 <server>"
                    )
            for m in USER_AT_HOST.finditer(line):
                user, host = m.group(1), m.group(2)
                if user in ALLOWED_USERS or host.startswith("<") or "example" in host:
                    continue
                hits.append(
                    f"{path}:{n}: ssh 用户名 `{user}@{host}` —— 改成 <user>@<server>"
                )
            for pat in FORBIDDEN_TERMS:
                if pat.search(line):
                    hits.append(
                        f"{path}:{n}: 旁路服务身份 —— 只存本地私有记录（~/workspace/dawnop-ops/），"
                        "仓库里别出现"
                    )

    if hits:
        print("服务器身份信息进了公开仓库：\n", file=sys.stderr)
        for h in hits:
            print(f"  {h}", file=sys.stderr)
        print(
            "\n约定：真实 IP / ssh 用户名 / 旁路服务身份只存本地私有记录；"
            "仓库里 ssh 写 <user>@<server>、旁路服务写「旁路 upstream」占位。",
            file=sys.stderr,
        )
        return 1

    print(f"ok: 已比对 {len(ips)} 个本站地址，未发现服务器身份信息")
    return 0


if __name__ == "__main__":
    sys.exit(main())
