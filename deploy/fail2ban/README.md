# fail2ban：WebDAV 鉴权爆破封禁

给 `dav.dawnop.com` 加的一个 jail：**连续 5 次「带凭证却被拒」就封 IP 1 小时**。
服务器上已有的 **sshd jail 不受影响**（各走各的文件，本目录不碰它）。

> ## 状态：已上线（2026-07-18 装到生产，`enabled = true`）
>
> 已按下面步骤装到生产并 `fail2ban-client reload`：`fail2ban-client status` 里有 `dawnop-dav-auth`，
> 其 `File list` 已确认是 `/var/log/nginx/dav.access.log`（不是 journald），`fail2ban-regex` 拿真日志
> 跑出 1 matched（一条 `auth=auth` 的鉴权失败）、`auth=noauth` 的正常挑战落在 missed。
>
> 它此前被 gate 的原因——`$remote_addr` 恒为 `127.0.0.1`（会封错对象）——**已于 2026-07-17 解除**
> （真实客户端 IP 恢复，做法见 `~/workspace/dawnop-ops/`）：dav 日志现在记的是真实客户端 IP。
> 下面的「装」一节保留为**重装 / 换机时的步骤**。

## 为什么要有它

`dav.dawnop.com` 每个方法都要 HTTP Basic 鉴权，鉴权走 bcrypt（约 250ms CPU）。
`webdav.dawn` 里那个 TTL 缓存按 `sha256("user:pass")` 建键，**只有密码对了才命中**——
攻击者每次换随机密码，次次落空、次次实打实烧一次 bcrypt，4 核约 **16 次/秒**就能把 CPU 打满。
既是爆破入口，也是 CPU 耗尽（DoS）入口。`/api/auth/login` 有 nginx `limit_req` 挡着，但 dav 是
**独立 vhost，那条规则盖不到**。

**为什么不用 limit_req**：Finder / rclone / Mountain Duck 挂载浏览时会突发打出成串请求
（一次列目录几十个 PROPFIND），按请求速率限流会把正常挂载打成 429。fail2ban 只盯**鉴权失败**，
正常客户端密码是对的，永远不进计数器。

**为什么不是「见 401 就封」**：401 是 Basic 鉴权的**正常第一步**——客户端首次访问 realm 不带凭证，
服务端回 401 + `WWW-Authenticate` 发起挑战。见 401 就封 = 封掉每一个正常挂载。真正的信号是
**401 且带了 Authorization**（凭证给了且被拒），nginx 侧靠 `auth=auth|noauth` 标记区分。

> **日志里只有「带没带凭证」这个存在性标记，绝不含凭证的值**（那是 base64 的管理员口令）。
> 改 nginx 站点配置（在 `~/workspace/dawnop-ops/`）的 `dawnop_dav` 格式时守住这条。

## 装

两个文件 + 一处 nginx 改动，**三者配套，缺一不可**：

```bash
# 1) nginx：dav vhost 的 access_log 换成带 auth= 标记的 dawnop_dav 格式。
#    nginx 站点配置在 ~/workspace/dawnop-ops/（不在本仓库），按那份笔记部署到 nginx 真读的站点文件。
#    写错文件 = 日志里没有 auth= 字段 = filter 一条都匹配不到，而 jail 状态永远 0 failed 且不报错，
#    看起来就像「没人攻击」。
#    （dawnop_dav 格式当前已在生产，重装本 jail 时通常不必再动 nginx。）

# 2) 过滤器 + jail
sudo cp deploy/fail2ban/filter.d/dawnop-dav-auth.conf /etc/fail2ban/filter.d/
sudo cp deploy/fail2ban/jail.d/dawnop-dav.conf        /etc/fail2ban/jail.d/

# 3) 生效
sudo fail2ban-client reload
```

`reload` 之前先确认日志已经是新格式（老格式没有 `auth=` 尾巴，一条都匹配不上）：

```bash
tail -2 /var/log/nginx/dav.access.log   # 期望每行以 auth=auth 或 auth=noauth 结尾
```

## 验

```bash
# 过滤器对不对：拿真实日志跑一遍
sudo fail2ban-regex /var/log/nginx/dav.access.log /etc/fail2ban/filter.d/dawnop-dav-auth.conf
```
看 `Failregex: N total`。判读要点：**matched 只该是 401+auth=auth 的行**；正常挂载的
401 挑战（auth=noauth）和 200/207 都该落在 missed 里。生产日志里平时应该几乎为 0——
不为 0 就是真有人在敲，或者管理员那边密码错了。

```bash
# jail 起来没、在读哪个文件、封了谁
sudo fail2ban-client status                 # jail 列表里应有 dawnop-dav-auth
sudo fail2ban-client status dawnop-dav-auth
```

`status dawnop-dav-auth` 里 **`File list` 必须是 `/var/log/nginx/dav.access.log`**。
如果那里是空的，说明 jail 去读 journald 了（见下方「坑」），此时它**不报错、也永远封不到人**。

## 把自己关在门外了

管理员自己挂载不上（多半是客户端里存了旧密码、后台一直重试）——1 小时后自动解，等不了就手工解：

```bash
sudo fail2ban-client set dawnop-dav-auth unbanip <你的IP>
curl -s ifconfig.me                            # 不知道自己出口 IP 就查一下
sudo fail2ban-client status dawnop-dav-auth    # 确认 Banned IP list 里没了
```

**注意封的只是 dav 的 http/https**，ssh 不受这个 jail 影响（sshd 是另一个 jail），
所以进不去网盘也照样能 ssh 上去解。急停整个 jail：

```bash
sudo fail2ban-client stop dawnop-dav-auth      # 临时停；reload 会恢复
```

固定 IP 想永久豁免，在 `/etc/fail2ban/jail.d/dawnop-dav.conf` 加 `ignoreip = 127.0.0.1/8 <你的IP>`。
但家宽 IP 会变，别指望它兜底。

## 坑（都是实测踩出来的）

- **`backend = auto` 必须显式写在 jail 里**。Debian/Ubuntu 的
  `/etc/fail2ban/jail.d/defaults-debian.conf` 在 `[DEFAULT]` 里设了 `backend = systemd`，
  **作用于所有 jail**；继承它的话本 jail 会去读 journald、把 `logpath` 完全忽略，
  nginx 日志一行都不解析，`status` 永远 0 failed，而且**不报任何错**。
- **failregex 里的日期字段是空方括号 `\[\]`**：fail2ban 先按 datepattern 把时间戳从行里抠掉，
  再拿 failregex 匹配剩下的部分。写成 `\[[^\]]+\]` 会 0 matched。
- **别给这个过滤器加从行首锚定的 datepattern**（如 `^[^\[]*\[({DATE})`）：nginx 会把 Basic 的
  用户名原样写进 `$remote_user` 栏，攻击者用 `x[y` 当用户名就能让 `[^\[]*` 提前止步、
  时间戳解析失败、整行被跳过 = 绕过封禁。交给默认探测器。
- **日志轮转**：logrotate 转走后 fail2ban 会自己跟上新文件，不用管；但**别把 dav 的
  access_log 关掉或改格式**，那等于把这个 jail 静默停用。
