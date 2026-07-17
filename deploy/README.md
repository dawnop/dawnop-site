# 部署指南

线上形态（M6 之后）：Nginx 托管前端静态产物，把 `/api` 反代到 **Dawn 后端
`127.0.0.1:8001`**（systemd `dawnop-dawn`）；`dav.dawnop.com` 是独立 vhost，同样反代到 Dawn。
FastAPI/uvicorn（`:8000`，systemd `dawnop-backend`）**已 `systemctl disable`**，但代码、venv、
`.env` 与 libsimple 都还在原地——它是紧急回滚目标，见
[`../backend-dawn/deploy/rollback-to-fastapi.sh`](../backend-dawn/deploy/rollback-to-fastapi.sh)。

> ## ⚠️ 先读这段再碰 nginx
>
> **443 不属于站点配置。** nginx 的 `stream` 模块占着 443，按 TLS 的 SNI 分流（不解密）：
> `bypass.invalid` → REDACTED:REDACTED（Claude 代理隧道，与本站无关），**其余 → `127.0.0.1:8443`**
> ——站点的 server 块全在 8443。这套在 `/etc/nginx/nginx.conf` 里，仓库版本是
> [`nginx-main.conf`](./nginx-main.conf)。**把站点块改回 `listen 443 ssl` = 抢端口 = 全站下线。**
>
> **真实客户端 IP 已丢失。** stream 终止 TCP 后从本机新建连接到 8443，且没开 `proxy_protocol`，
> 故 http 层 `$remote_addr` **恒为 `127.0.0.1`**（实测最近 200 条访问日志无一例外）。
> 于是：日志里没有来源 IP；任何按 `$binary_remote_addr` 的 `limit_req` 都是**全站共用一个桶**
> （playground 的 `playrun`/`playcheck` 就这么静默失效了，2026-07-14 起）；按 IP 封禁只会封到
> 分流层自己。**修好之前别往 nginx 加任何按 IP 的防护**，那只会制造「以为堵上了」的错觉。
>
> 这两件事在 2026-07-17 之前**都不在版本控制里**，本文件当时还写着「Nginx 监听 80/443」。
> 仓库与生产曾双向漂移：仓库单方面更新注释、生产单方面改结构，而本文件把仓库那份称作「权威」。
> 现在 [`nginx.conf`](./nginx.conf) 是以生产实际配置为准重建的。改 nginx 前先
> `readlink -f /etc/nginx/sites-enabled/dawnop` 确认自己在改 nginx 真读的那个文件。

下文用占位符，按实际替换：`<user>` 登录用户（需 sudo）、`<server>` 服务器地址、`dawnop.com` 域名。

## 服务器上的东西在哪

| 路径 | 是什么 |
|---|---|
| `/opt/dawnop-dawn/` | **生产后端**：`backend-dawn.jar` + `lib/`（CI artifact），root 所有 |
| `/opt/dawnop/data/dawnop.db` | **数据库**（见下方「数据库」一节——**不在** `/opt/dawnop/backend/`） |
| `/opt/dawnop/backend/` | FastAPI 代码 + `.venv` + **`.env`** + `extensions/libsimple.so` |
| `/var/www/dawnop/dist/` | 前端静态产物 |

`.env` 与 libsimple 住在 `/opt/dawnop/backend/` 是历史原因（FastAPI 时代建的），
Dawn 服务直接复用：`dawnop-dawn.service` 里 `DAWNOP_ENV` 与 `DAWN_SIMPLE_EXT` 指过去。
所以那个目录**不是遗迹，删了生产会挂**。

---

## 一、日常更新（常态）

**后端**（改了 `backend-dawn/`）：push 到 main，等 CI 绿，然后
```bash
ssh <user>@<server> 'sudo bash -s' < backend-dawn/deploy/deploy.sh          # 最新的绿 main
ssh <user>@<server> 'sudo bash -s' < backend-dawn/deploy/deploy.sh <sha>    # 指定提交
```
脚本从 CI artifact 取 jar + lib/（**部署的就是 CI 测过的那个构建**），校验 manifest
Class-Path 齐全，装好后 `systemctl restart dawnop-dawn` 并健康检查；不健康自动回滚到上一个
构建。需要服务器上有 `/opt/dawnop-dawn/.github-token`（fine-grained PAT，Actions 只读，chmod 600）。

**前端**：
```bash
cd frontend && npm run build
rsync -az --delete frontend/dist/ <user>@<server>:/var/www/dawnop/dist/
# 静态资源带哈希、index.html 不缓存，发完即时生效
```

**改了 Nginx/gzip 配置**：`sudo nginx -t && sudo systemctl reload nginx`。

**紧急回滚到 FastAPI**：
```bash
ssh <user>@<server> 'sudo bash /opt/dawnop-dawn/rollback-to-fastapi.sh'
```
把 uvicorn 拉起来、nginx 的 `/api` 与 dav 指回 `:8000`。Dawn 服务不动，切回去就是反操作。
两套共用同一个 SQLite 文件（WAL），**没有数据迁移要撤**——切流与回滚都是纯路由变更。

---

## 二、首次部署 / 重建服务器

> 顺序有讲究：先把 `/opt/dawnop/backend`（`.env` + libsimple + 回滚目标）立起来，
> 再建库目录，最后才是 Dawn 服务——后者依赖前两者。

### 0. 装依赖、建目录
```bash
sudo apt-get update
sudo apt-get install -y nginx rsync python3-venv python3-pip openjdk-21-jre-headless
sudo mkdir -p /opt/dawnop/backend /opt/dawnop/data /opt/dawnop-dawn /var/www/dawnop/dist
sudo chown -R <user>:<user> /opt/dawnop /var/www/dawnop
sudo chown dawnop:dawnop /opt/dawnop/data     # 服务以 dawnop 身份写库
```

### 1. FastAPI 侧（提供 .env / libsimple / 回滚能力）
```bash
# 本地传代码
rsync -az --delete \
  --exclude='.venv' --exclude='__pycache__' --exclude='*.db' --exclude='.pytest_cache' \
  --exclude='extensions/libsimple.*' \
  backend/ <user>@<server>:/opt/dawnop/backend/

# 服务器上
cd /opt/dawnop/backend
python3 -m venv .venv
# ⚠️ 国内服务器 pip 直连 PyPI 会失败，务必用镜像
./.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip
./.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
```

`.env`（**不进仓库**，以 `backend/.env.example` 为模板）：
```bash
cd /opt/dawnop/backend
python3 -c "import secrets;print('SECRET_KEY='+secrets.token_urlsafe(48))"   # 别用示例里的弱默认
chmod 600 .env
```

> ⚠️ **`DATABASE_URL` 必须写绝对路径指向共用库**：
> ```
> DATABASE_URL=sqlite:////opt/dawnop/data/dawnop.db     # 四个斜杠 = 绝对路径
> ```
> `.env.example` 里的默认是**相对路径** `sqlite:///./dawnop.db`，解析成
> `/opt/dawnop/backend/dawnop.db`——那是 M6 搬库**之前**的位置。若服务器上的 `.env`
> 还留着这个默认值，回滚脚本会把站点切到一个**旧库**上，而且不报错、只是数据回到过去。
> 现在就去确认一次：
> ```bash
> grep DATABASE_URL /opt/dawnop/backend/.env
> ls -l /opt/dawnop/backend/dawnop.db 2>/dev/null && echo "↑ 存在就更可疑：确认它不是被当成生产库"
> ```
> Dawn 侧不受影响（`dawnop-dawn.service` 用 `DAWN_DB_PATH` 写死了绝对路径）。

**libsimple**（中文分词，写 `articles` 必须有它——FTS 触发器用 `tokenize='simple'`）：
```bash
# 服务器连不上 GitHub releases，fetch_simple_ext.py 在上面跑不通 → 本地下好再传
python backend/scripts/fetch_simple_ext.py        # 本地（若本地也连不上，手动下 linux 版）
scp backend/extensions/libsimple.so <user>@<server>:/opt/dawnop/backend/extensions/
```

### 2. 初始化库与管理员
```bash
cd /opt/dawnop/backend
./.venv/bin/python scripts/seed_admin.py    # 读 .env 的 ADMIN_*，建表 + 默认页面
ls -l /opt/dawnop/data/dawnop.db            # 确认库建在这里，不是 backend/ 下
```

### 3. Dawn 后端（生产）
```bash
# 装 systemd unit 并设为开机自启
sudo cp backend-dawn/deploy/dawnop-dawn.service /etc/systemd/system/    # 或 scp 后 mv
sudo systemctl daemon-reload
sudo systemctl enable dawnop-dawn     # deploy.sh 只 restart，不 enable——首次必须手动开
sudo systemctl disable --now dawnop-backend   # uvicorn 退役；回滚脚本会临时把它拉起来

# 放 GitHub token 供 deploy.sh 拉 artifact
sudo install -m 600 /dev/stdin /opt/dawnop-dawn/.github-token   # 粘贴 PAT，Ctrl-D

# 首次部署（脚本装 jar + lib/、restart、健康检查，不健康自动回滚）
ssh <user>@<server> 'sudo bash -s' < backend-dawn/deploy/deploy.sh
curl -s http://127.0.0.1:8001/api/health    # 期望 {"status":"ok",...}
systemctl is-enabled dawnop-dawn            # 期望 enabled（否则重启后站点不起）
```

> `dawnop-dawn.service` 开了 `ProtectSystem=strict` + `ReadWritePaths=/opt/dawnop/data`：
> **服务只能写库那一个目录**。这正是库放在 `/opt/dawnop/data/` 而非 `backend/` 下的原因；
> 改库路径必须同步改 `ReadWritePaths`，否则服务起得来、写库时报只读文件系统。

### 4. 前端与 Nginx
```bash
cd frontend && npm run build
rsync -az --delete frontend/dist/ <user>@<server>:/var/www/dawnop/dist/

# 站点依赖的两个文件先就位（否则 nginx -t 直接失败）
sudo mkdir -p /etc/nginx/snippets
sudo cp deploy/nginx-snippets/dawnop-proxy.conf /etc/nginx/snippets/   # /api 的公共 proxy 头
# nginx-main.conf 不是完整文件，是「该并进 /etc/nginx/nginx.conf 的那几段」：
#   顶层的 stream{}（SNI 分流，占着 443）+ http{} 里的 playrun/playcheck zone。手工合并，别覆盖。

sudo cp deploy/nginx.conf /etc/nginx/sites-enabled/dawnop        # 含 SPA 回退 + /api→8001 + 各子域名 vhost
sudo rm -f /etc/nginx/sites-enabled/default
sudo cp deploy/gzip.conf /etc/nginx/conf.d/gzip.conf             # gzip_static 直发预压缩 .gz
sudo nginx -t && sudo systemctl reload nginx
```

> **为什么直接写 `sites-enabled/` 而不是 `sites-available/` + 软链**：nginx 只 `include
> sites-enabled/*`，而生产上 `sites-enabled/dawnop` 是**普通文件不是软链**，`sites-available/dawnop`
> 是一份没人读的陈旧副本（还停留在 `listen 443` 的世代）。这两个文件已经分家很久了。
> **千万别照老文档跑 `ln -sf sites-available/dawnop sites-enabled/dawnop`**——那会把 nginx 真读的
> 文件换成指向那份陈旧副本的软链，等于一键部署一个会抢 443 的配置，站点当场下线。
>
> 想把布局收回「软链」这个更干净的形态是可以的，但要先让两个文件内容一致再动，且 `nginx -t`
> 之后再 reload。动之前先 `readlink -f /etc/nginx/sites-enabled/dawnop` 看清现状。
>
> 备份 nginx 配置**别放 `sites-enabled/`**——nginx 会把备份文件也当配置加载。

### 5. 验证
```bash
curl -s http://127.0.0.1/api/health
curl -s https://dawnop.com/api/health
curl -sI https://dawnop.com/assets/<某个js> -H 'Accept-Encoding: gzip' | grep -i content-encoding  # 期望 gzip
```
浏览器过一遍：首页、文章页（公式/代码高亮/viz）、`/admin/login`、文件管理、⌘K 搜索。

HTTPS 与证书（含 `storage.` / `cdn.` / `dav.` 子域名的通配符证书与自动续期）见
[`https-ssl-setup.md`](./https-ssl-setup.md)。

---

## 三、注意事项

- **数据库**：SQLite 在 **`/opt/dawnop/data/dawnop.db`**，两套后端共用同一个文件（WAL 模式）。
  - 权威来源：`dawnop-dawn.service` 的 `DAWN_DB_PATH`，与 `nginx-cutover.md` 的前置条件。
  - **备份的就是这个文件**（连同 `-wal`/`-shm`；热备份用 `sqlite3 <db> ".backup out.db"`，
    别直接 cp 一个正在写的库）。
  - 不在任何 rsync 范围内（`*.db` 已 exclude），更新代码不会动它。
  - 改了模型结构需重建库（本项目无迁移）：删库后重跑 `seed_admin.py`。
  - 裸 `sqlite3` 打开这个库后**改 `articles` 会报 `no such tokenizer: simple`**——
    FTS 触发器要 libsimple，命令行没加载它。要么 `.load` 它，要么走 API。
- **密钥/账号**：只在服务器的 `/opt/dawnop/backend/.env`（权限 600），仓库只有 `.env.example`。
- **端口**：对外只有 80/443。443 归 nginx 的 `stream` 块做 SNI 分流（见开头的警告）：
  `bypass.invalid`→REDACTED `:REDACTED`，其余→站点的 `127.0.0.1:8443`。
  `:8001` = Dawn（生产），`:8000` = uvicorn（已 disable，回滚时才起），`:8087` = Dawn playground，
  `:8222` = Vaultwarden 容器。除 80/443 外全部只监听 `127.0.0.1`。
- **CDN**：`cdn.dawnop.com` 回源分发 `/assets`，前端 `VITE_CDN_BASE` 在 `.env.local` 配（不入库）。
- **fail2ban**：目前**只有 sshd 一个 jail 在跑**。仓库里的 `dawnop-dav-auth`（封 `dav.dawnop.com`
  的 WebDAV 鉴权爆破——Basic 鉴权每次都烧 bcrypt，是 CPU 耗尽面）已写好并用真日志验证过，但
  **`enabled = false`**：`$remote_addr` 恒为 `127.0.0.1`（见开头），封禁会封到 SNI 分流层自己、
  放跑真攻击者。恢复真实 IP 后把它改 true 即可，filter 不用动。它依赖 dav vhost 的 `dawnop_dav`
  日志格式，**改 nginx 日志格式会静默停用它**。装法/验证/把自己封了怎么解，见
  [`fail2ban/README.md`](./fail2ban/README.md)。
