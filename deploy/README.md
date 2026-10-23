# 部署指南

线上形态（M6 之后）：Nginx 托管前端静态产物，把 `/api` 反代到 **Dawn 后端
`127.0.0.1:8001`**（systemd `dawnop-dawn`）；`dav.dawnop.com` 是独立 vhost，同样反代到 Dawn。
FastAPI/uvicorn（`:8000`，systemd `dawnop-backend`）**已 `systemctl disable`**，但代码、venv、
`.env` 与 libsimple 都还在原地——它是紧急回滚目标，见下面的
[「四、回滚安全链」](#四回滚安全链)（脚本是
[`rollback-to-fastapi.sh`](../backend-dawn/deploy/rollback-to-fastapi.sh) 与
[`return-to-dawn.sh`](../backend-dawn/deploy/return-to-dawn.sh)）。

> ## ⚠️ nginx 配置不在这个仓库
>
> nginx 的所有配置在 `~/workspace/dawnop-ops/`（私有，不推送）：站点 vhost、443 接入、`/api` 反代、
> 真实 IP 恢复手册都在那儿。**改 nginx 看那份笔记**（含它开头的几条警告）。CI 守卫 + pre-push hook
> （`scripts/check-no-server-identity.py`）挡住服务器身份信息进公开仓库。
>
> 本文件只讲后端、前端、数据库、systemd 那部分。

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
构建。需要服务器上有 `/opt/dawnop-dawn/.github-token`（fine-grained PAT，Actions 只读，chmod 600）
和 `/opt/dawnop-dawn/.deploy-proxy`（见下）。

> **不要在调用前加 `https_proxy=` 之类的前缀**——脚本自己处理代理，而那个前缀会坑你，
> 见下一段。

**关于代理**：artifact 的**字节**不来自 `api.github.com`，它 302 到 Azure blob，而那一段从
本机直连**慢到不可用**——同一个 10MB artifact 实测：直连 11.7 KB/s（10MB 要 ~15 分钟，还未必
能跑完），走本机正向代理 290 KB/s（35 秒）。GitHub **API 本身直连是好的**（~1.8s），脚本
**只代理下载那一次 curl**。代理地址写在 `/opt/dawnop-dawn/.deploy-proxy`（一行 `host:port`，
不入库——同 `.github-token` 的理由，机器的旁路配置在 `~/workspace/dawnop-ops/`）；文件不在也能
部署，只是慢，脚本会明说。

> ⚠️ **为什么不能自己 `export http_proxy`**：脚本第 6 步的健康检查是
> `http://127.0.0.1:8001/api/health`，`http_proxy` 会把它一起收走，代理接下连接然后**挂住**，
> 40 次重试全超时——于是脚本**把一次完全正常的部署回滚掉**。实测确认：只设 `https_proxy`
> 不会触发（curl 按 URL scheme 选代理变量，健康检查是 http），设 `http_proxy` 才会。
> 脚本现在用 `curl --proxy` 只作用于下载那一次，并且**主动 unset 继承来的代理变量**，
> 所以这个坑现在按不出来了。

**前端**：
```bash
cd frontend && npm run build
rsync -az --delete frontend/dist/ <user>@<server>:/var/www/dawnop/dist/
# 静态资源带哈希、index.html 不缓存，发完即时生效
```

**404 与 SPA 路由**：私有运维仓的主站配置只给已知前端路由回退 `index.html`；未知路径
以 HTTP 404 加载前端壳。文章、内容页和标签页通过 `/api/site-route/{kind}/{slug}` 做只读存在性检查，
缺失资源同样返回 404，后端故障保留 5xx。上线顺序：先部署含存在性接口的 Dawn 后端，再上传前端，
最后更新 Nginx 配置。旧的仅摘要上传链接按用户要求在后端启动升级时清除，已上传文件保留。

**改了 Nginx/gzip 配置**：`sudo nginx -t && sudo systemctl reload nginx`。

**紧急回滚到 FastAPI**（从本地把脚本 pipe 过去跑，别跑服务器上的副本，理由见下）：
```bash
ssh <user>@<server> 'sudo bash -s' < backend-dawn/deploy/rollback-to-fastapi.sh   # 切到 FastAPI :8000
ssh <user>@<server> 'sudo bash -s' < backend-dawn/deploy/return-to-dawn.sh        # 切回 Dawn :8001
```

> ⚠️ **服务器上不该有这两个脚本的副本**：`deploy.sh` 只装 jar 与 `lib/`，本文档也没有装它们
> 的一步。M6 切流时手工放过一份 `rollback-to-fastapi.sh` 在 `/opt/dawnop-dawn/`，50 行、
> 早于 2026-07 的重写，不含现在这条安全链的任何一件（无库身份核验、无哨兵、无逃生阀），
> 而且它 `systemctl enable --now`（持久化到重启）又不放哨兵，等于回滚到一份会写库、
> 且 `/api/fm` 全开对着共用七牛桶的旧代码。**已于 2026-08-14 删除**，
> 现在由 `scripts/check_server_drift.py` 的 `stale-scripts` 组盯着它不再长回来。
> 两个脚本跑完在结尾印的「下一步」就是上面这两行（由
> `backend/tests/test_rollback_chain.py` 钉住，它们曾经印过一个 `/opt/dawnop-dawn/` 下的
> 不存在路径）。

两套共用同一个 SQLite 文件，**没有数据迁移要撤**：切流与回滚都是路由变更加一个守护
开关（回滚期间 `/api/fm` 关闭、回 503，见「四」）。
（`journal_mode` 实测是 `delete`，不是 WAL——这里以前写着 WAL，据此推导出来的权限配方是错的，
见第二节第 2 步。）
细节见下面「四、回滚安全链」，**第一次用之前先把那一节的两个前置条件配好**，否则回滚脚本
会在核验那一步停下来（它宁可不切，也不切到一个说不清的进程上）。

---

## 二、首次部署 / 重建服务器

> 顺序有讲究：先把 `/opt/dawnop/backend`（`.env` + libsimple + 回滚目标）立起来，
> 再建库目录，最后才是 Dawn 服务——后者依赖前两者。

### 0. 装依赖、建目录
```bash
sudo apt-get update
sudo apt-get install -y nginx rsync python3-venv python3-pip openjdk-21-jre-headless
# Dawn 服务以 dawnop 身份跑（dawnop-dawn.service 的 User=/Group=），这个账号要先存在
id dawnop || sudo useradd --system --no-create-home --shell /usr/sbin/nologin dawnop
sudo mkdir -p /opt/dawnop/backend /opt/dawnop/data /opt/dawnop-dawn /var/www/dawnop/dist
sudo chown -R <user>:<user> /opt/dawnop /var/www/dawnop
# /opt/dawnop/data 先留给 <user>：第 2 步的 seed_admin.py 以 <user> 身份在里面建库，
# 目录若已经归 dawnop，那一步会以「unable to open database file」失败。建完库再交出去。
```

### 1. FastAPI 侧（提供 .env / libsimple / 回滚能力）
```bash
# 本地传代码
# ⚠️ .env 与 .rollback-probe 必须排除：本地 backend/.env 是开发用的（DATABASE_URL 是相对
#    路径），传上去会盖掉生产的那份；而 --delete 会把服务器上本地没有的 .rollback-probe
#    删掉，回滚就会卡在核验那一步。两者都只该在服务器上生成，见下面与「四」。
rsync -az --delete \
  --exclude='.venv' --exclude='__pycache__' --exclude='*.db' --exclude='.pytest_cache' \
  --exclude='extensions/libsimple.*' --exclude='.env' --exclude='.rollback-probe' \
  backend/ <user>@<server>:/opt/dawnop/backend/

# 服务器上
cd /opt/dawnop/backend
python3 -m venv .venv
# ⚠️ 国内服务器 pip 直连 PyPI 会失败，务必用镜像
./.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip
./.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
```

`.env`（**不进仓库**，以 `backend/.env.example` 为模板；上面的 rsync 只送来 `.env.example`）：
```bash
cd /opt/dawnop/backend
cp .env.example .env
python3 -c "import secrets;print('SECRET_KEY='+secrets.token_urlsafe(48))"   # 别用示例里的弱默认
vi .env      # 填 SECRET_KEY / QINIU_* / ADMIN_*，并按下面那条警告改掉 DATABASE_URL
chmod 600 .env
```
`ROLLBACK_PROBE_HEADER` 也在这一份 `.env` 里，和它配套的 `.rollback-probe` 文件一起配，
见「[四、回滚安全链](#四回滚安全链)」的前置条件。**这一步跳过的代价要到回滚当天才付**：
回滚脚本会停在核验那一步，不切流。

> ⚠️ **这份 `.env` 被两个不同的解析器读**：Dawn 的 `backend-dawn/src/config.dawn` 与
> python-dotenv（pydantic-settings 用的那个）。两边只有 trim 和剥成对引号是一致的；
> 行内注释 `#`、`${VAR}` 插值、`\n` 转义、`export ` 前缀，python-dotenv 都处理，Dawn 都不处理。
> 所以**值里不要出现 `#`、`$`、`\`、引号、`export `，`=` 两侧也不要留空白**。
> 越界不会报错，只会让同一行在两个后端读出两个值：`SECRET_KEY=abc#def` 在 FastAPI 是 `abc`、
> 在 Dawn 是 `abc#def`，于是 Dawn 签发的 token 回滚到 FastAPI 后全部失效。
> 现状已量清、都安全：`SECRET_KEY` 与 `ROLLBACK_PROBE_HEADER` 是 `token_urlsafe`（只有
> `A-Za-z0-9_-`），七牛 AK/SK 是 base64url，都不含危险字符；唯一可能含 `#` 的 `ADMIN_PASSWORD`
> 只被 `backend/scripts/seed_admin.py` 读，Dawn 侧根本不读它，没有分歧的余地。
> **两个解析器都不改**：让 Dawn 剥行内注释，会把一把恰好含 `#` 的真密钥当场截断；让它遇到
> 歧义就拒绝启动，等于给生产加一个新的宕机方式。约束落在模板上更便宜，
> `scripts/check_config_defaults.py` 已经在 CI 里逐行守着 `.env.example`。

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

**装 uvicorn 的 systemd 单元**（现在就装：它是回滚目标，不装的话第 3 步的
`systemctl disable dawnop-backend` 会报 unit 不存在，回滚脚本也没有单元可拉）：
```bash
sudo cp deploy/dawnop-backend.service /etc/systemd/system/    # 或 scp 后 mv
sudo systemctl daemon-reload
```
> 单元里的 `User=dawnop` / `WorkingDirectory=/opt/dawnop/backend` / `ExecStart=` 不是随便写的：
> 回滚脚本逐字节比对进程的 argv、cwd 与有效 UID:GID。**改这个单元就要同步改
> `backend-dawn/deploy/rollback-to-fastapi.sh` 里的 `FASTAPI_*` 常量**（两个脚本共享那段，
> `backend/tests/test_rollback_chain.py` 会比对），否则回滚会在核验那一步停下。

### 2. 初始化库与管理员
```bash
cd /opt/dawnop/backend
./.venv/bin/python scripts/seed_admin.py    # 读 .env 的 ADMIN_*，建表 + 默认页面
ls -l /opt/dawnop/data/dawnop.db            # 确认库建在这里，不是 backend/ 下

# 库建好了再安排权限。**两个单元都以 dawnop 跑**（`dawnop-dawn` 与 `dawnop-backend` 的
# User= 都是 dawnop），所以这里只有一个身份要伺候，不需要任何跨账号的组安排。
sudo chown -R dawnop:dawnop /opt/dawnop/data
sudo chmod 750 /opt/dawnop/data
sudo chmod 644 /opt/dawnop/data/dawnop.db
```
> 以上四行是 **2026-08-14 在生产机上实测抄下来的**，不是推导的：`/opt/dawnop/data` =
> `drwxr-x--- dawnop:dawnop`，库 = `-rw-r--r-- dawnop:dawnop`，`journal_mode=delete`。
>
> 这里曾经写着一套 `chmod 2775` + `chmod 664` + `usermod -aG dawnop dawn` 的配方，理由是
> 「回滚目标 uvicorn 以 dawn 跑」加「WAL 模式要在同目录建 -wal/-shm」。**两个前提实测都不成立**：
> 装机单元的 `User=` 是 dawnop（仓库副本那时写着 dawn，两边漂了），库也不是 WAL 而是 delete。
> 照那套配方在一台正常工作的机器上执行，会把库目录从 0750 放宽到 0775 并把 `dawn` 拉进
> `dawnop` 组，纯属白送权限。留在这里当个记号：**运维手册里没被机器核对过的那部分，
> 默认当它是错的。** 现在这份由 `scripts/check_server_drift.py` 盯着单元文件那一半。

### 3. Dawn 后端（生产）
```bash
# 装 systemd unit 并设为开机自启
sudo cp backend-dawn/deploy/dawnop-dawn.service /etc/systemd/system/    # 或 scp 后 mv
sudo systemctl daemon-reload
sudo systemctl enable dawnop-dawn     # deploy.sh 只 restart，不 enable——首次必须手动开
sudo systemctl disable --now dawnop-backend   # uvicorn 退役；回滚脚本会临时把它拉起来

# 放 GitHub token 供 deploy.sh 拉 artifact
sudo install -m 600 /dev/stdin /opt/dawnop-dawn/.github-token   # 粘贴 PAT，Ctrl-D

# 代理地址（artifact 下载那一段直连慢到不可用，理由见「一、日常更新」）
sudo install -m 600 /dev/stdin /opt/dawnop-dawn/.deploy-proxy   # 一行 host:port，Ctrl-D

# 首次部署（脚本装 jar + lib/、restart、健康检查）
ssh <user>@<server> 'sudo bash -s' < backend-dawn/deploy/deploy.sh

# 以下两条在服务器上跑
curl -s http://127.0.0.1:8001/api/health    # 期望 {"status":"ok",...}
systemctl is-enabled dawnop-dawn            # 期望 enabled（否则重启后站点不起）
```

> **首次部署没有自动回滚**：日常更新时 `deploy.sh` 会把在跑的 jar 存进 `/opt/dawnop-dawn/.prev`，
> 新构建不健康就还原回去；首次部署没有「上一个构建」可还原，脚本会在开头就说明这一点，
> 起不来时它把新 jar 留在原地、服务停着、退出码非 0。此时看
> `journalctl -u dawnop-dawn -n 50 --no-pager`。

> `dawnop-dawn.service` 开了 `ProtectSystem=strict` + `ReadWritePaths=/opt/dawnop/data`：
> **服务只能写库那一个目录**。这正是库放在 `/opt/dawnop/data/` 而非 `backend/` 下的原因；
> 改库路径必须同步改 `ReadWritePaths`，否则服务起得来、写库时报只读文件系统。

### 4. 前端
```bash
cd frontend && npm run build
rsync -az --delete frontend/dist/ <user>@<server>:/var/www/dawnop/dist/
```

### 4b. Nginx
nginx 的所有配置在 `~/workspace/dawnop-ops/`（私有，不推送）。按那份笔记的「部署 / 改动」一节操作，
`nginx -t` 过了再 `reload`；**改 nginx 前必读它开头的警告**。`deploy/gzip.conf` 是仓库里唯一留下的
nginx 片段（`gzip_static` 直发预压缩 `.gz`，与 443 接入无关），放 `/etc/nginx/conf.d/`。

**上传链接（`/api/drop*`）是匿名端点**，建议在 nginx 上给它单独加 `limit_req`。

**一步式 `PUT /api/drop/files/{name}` 与 WebDAV `PUT` 的拒绝都发生在读请求体之前**（web 5.1 的路由
guard 与 `body_limit`，dawn v0.81.0 起；修的是
[dawn-lang#310](https://github.com/dawnop/dawn-lang/issues/310)，此前框架先把整包落盘才进处理函数）：
- drop：token、链接状态（未吊销/未过期/未用尽/目录在）、名字、`Content-Length`（必须有、不许 chunked，
  否则 411）、声明长度不超过单文件上限与剩余总额（否则 413），全部在后端读第一个字节之前判完，被拒的
  请求不建临时文件。后端自己的落盘上限是 5GiB（任何链接可设的单文件上限的最大值），流中途超过即 413
  并删残文件。
- WebDAV：除 `OPTIONS` 外所有动词先过 Basic 鉴权再读体（匿名直接 401），`PUT` 后端上限 512MiB，
  与 dav vhost 的 `client_max_body_size 512m` 对齐。

于是 nginx 的 `client_max_body_size` 不再是挡匿名落盘的那道闸，而是**生产上一步式 PUT 的实际单文件
上限**：后端上限（drop 5GiB、dav 512MiB）只在 nginx 放行之后才起作用。有一条依赖要随 nginx 一起配：
nginx 默认**先把整个请求体缓冲到它自己的临时文件**再转发（`proxy_request_buffering on`），这时后端的
guard 虽然不读体，nginx 已经替匿名请求把体收完了。所以 `/api/drop` 的 location 要
`proxy_request_buffering off`（边收边转，后端 guard 一拒，连接即断），再把 `client_max_body_size`
放宽到想给 curl 一步式的上限（不必超过 5GiB）。dav vhost 同理（它的 PUT 本来就流式落盘）。
配置写在私有 ops 仓，不在本仓库。

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

- **数据库**：SQLite 在 **`/opt/dawnop/data/dawnop.db`**，两套后端共用同一个文件。
  `journal_mode` **实测是 `delete` 不是 WAL**（2026-08-14 读文件头第 18 字节 = 1，
  且目录里没有 `-wal`/`-shm`）。这里以前写着 WAL，别再照抄。
  - 权威来源：`dawnop-dawn.service` 的 `DAWN_DB_PATH`，与 `nginx-cutover.md` 的前置条件。
  - **备份的就是这个文件**，每天自动一份，见「五、库备份」。热备份用
    `sqlite3 <db> ".backup out.db"`，别直接 cp 一个正在写的库。
  - 不在任何 rsync 范围内（`*.db` 已 exclude），更新代码不会动它。
  - 改了模型结构需重建库（本项目无迁移）：删库后重跑 `seed_admin.py`。
  - 裸 `sqlite3` 打开这个库后**改 `articles` 会报 `no such tokenizer: simple`**——
    FTS 触发器要 libsimple，命令行没加载它。要么 `.load` 它，要么走 API。
- **密钥/账号**：只在服务器的 `/opt/dawnop/backend/.env`（权限 600），仓库只有 `.env.example`。
- **端口**：对外只有 80/443（443 的接入方式见 `~/workspace/dawnop-ops/`）。后端本机端口：
  `:8001` = Dawn（生产），`:8000` = uvicorn（已 disable，回滚时才起），`:8087` = Dawn playground，
  `:8222` = Vaultwarden 容器。这些全部只监听 `127.0.0.1`。
- **CDN**：`cdn.dawnop.com` 回源分发 `/assets`，前端 `VITE_CDN_BASE` 在 `.env.local` 配（不入库）。
- **fail2ban**：两个 jail 在跑——`sshd` 与 `dawnop-dav-auth`（封 `dav.dawnop.com` 的 WebDAV 鉴权爆破——
  Basic 鉴权每次都烧 bcrypt，是 CPU 耗尽面）。后者**已于 2026-07-18 上线**（`enabled = true`，读
  `/var/log/nginx/dav.access.log`）：真实客户端 IP 已恢复（做法见 `~/workspace/dawnop-ops/`），封的才是
  真来源。它依赖 dav vhost 的 `dawnop_dav` 日志格式（尾巴的 `auth=` 标记），
  **改/关 nginx 那个日志格式会静默停用它**。重装/验证/把自己封了怎么解，见
  [`fail2ban/README.md`](./fail2ban/README.md)。

---

## 四、回滚安全链

回滚是全仓最少被执行的代码：着火之前没有任何东西会跑它。所以这条链上的每一步都拒绝
「看起来对」——它要么拿到可核对的证据，要么停下来不切流。

两个脚本，互为反操作，都以 root 跑：

| 脚本 | 做什么 |
|---|---|
| `backend-dawn/deploy/rollback-to-fastapi.sh` | Dawn :8001 → FastAPI :8000 |
| `backend-dawn/deploy/return-to-dawn.sh` | FastAPI :8000 → Dawn :8001 |

> ⚠️ **回滚落到的是一个未加固的进程。** `deploy/dawnop-backend.service` 上没有任何 systemd
> 沙箱指令，而生产的 `backend-dawn/deploy/dawnop-dawn.service` 有四条：`NoNewPrivileges`、
> `ProtectSystem`（配 `ReadWritePaths=/opt/dawnop/data`）、`ProtectHome`、`PrivateTmp`。
> 所以回滚不只是换一个后端，它同时把这四层约束一起摘掉：回滚期间那个进程能碰的东西比
> Dawn 那个多。
>
> 这是有意留着的，不是漏了。那条路径至今没有端到端跑过（#249），而 `ProtectHome` /
> `ProtectSystem` 与 venv 路径、`libsimple.so` 的加载、`.env` 的读取都可能冲突，冲突只会在
> 真正需要回滚的那一刻暴露；给一条没跑通过的应急路径加沙箱，风险是把回滚本身弄坏。
> 补加固等演练之后再评估。这一条是让你知道自己在接受什么，不是叫你别回滚。

### 前置条件（配一次，回滚当天没时间配）

1. **探针秘密**，两处内容必须一致：
   ```bash
   secret=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
   printf '%s' "$secret" | sudo install -m 600 /dev/stdin /opt/dawnop/backend/.rollback-probe
   echo "ROLLBACK_PROBE_HEADER=$secret" | sudo tee -a /opt/dawnop/backend/.env
   ```
   脚本从 `.rollback-probe` 读（以 root 跑，文件是 root 的 600），FastAPI 从 `.env` 读。
   **不一致 = 探针回 404**，回滚会在核验那一步停下。留空则探针整体关闭（任何请求都 404，
   不泄露它存在）。`.env` 里本来就有一行空的 `ROLLBACK_PROBE_HEADER=`（来自 `.env.example`），
   `tee -a` 追加的这行在后面、读取时生效；嫌重复可以把前一行删掉。
2. **`DATABASE_URL` 是绝对路径**（见「二、1」那条警告）。这正是核验要抓的东西。

### 回滚脚本按什么顺序做事

1. 放哨兵 `/etc/dawnop/fastapi-file-routes-disabled`，**然后**才 `systemctl restart`。
   守卫是进程级闩存，起来之后才放的哨兵这一代进程看不见；用 `restart` 而不是
   `enable --now`，是为了保证读到哨兵的一定是新进程。受守护 = `/api/fm` 关闭（回 503）。
2. 拉起 uvicorn，等 `:8000/api/health`。
3. **核验（在动 nginx 之前）**：单元 cgroup / argv / cwd / 有效 UID:GID、
   `/proc/$PID/exe` 是不是钉住的解释器、单元定义与进程环境里都没有 `LD_*` / `PYTHON*`、
   库身份对得上、`/api/fm` 确实是 503。
   > 顺序不是随便排的。`systemctl show -p Environment` 看不见 `EnvironmentFile=` 指向的
   > `.env` 的内容——补上这个盲区的只有 `/proc/$PID/environ`，而它要求进程已经在跑。
   > 「进程起来了、流量还没切过去」是唯一能既看得见真实进程环境、又还来得及不切流的窗口。
4. 改 nginx（备份 → 逐条核对待替换的字符串 → `sed -i` → `nginx -t` → reload）。
5. 切流后复验公网。

### 被加载器环境检查挡住时（`ROLLBACK_ALLOW_ENV`）

第 3 步那条「单元定义与进程环境里都没有 `LD_*` / `PYTHON*`」是**刻意宽的**：`PYTHONUNBUFFERED`、
`PYTHONDONTWRITEBYTECODE` 这种不影响加载行为的变量也会被拦下来。宽是为了不用维护一张
「哪些 `PYTHON*` 是安全的」的名单，那张名单会过期。

**正常情况下你根本用不到这一节。** 实测：`deploy/dawnop-backend.service` 里没有任何
`Environment=`（只有一个 `EnvironmentFile=/opt/dawnop/backend/.env`），`backend/.env.example`
里也没有 `PYTHON*` / `LD_*`，`ExecStart` 直接调 venv 里的二进制、不做 `activate`。所以生产
大概率不会触发它。但服务器上真实的 `.env` 在仓库里看不见，不能排除。

真被挡住、且你**看过那个变量、确认它不影响加载行为**时，用逃生阀显式放行：

```bash
ssh <user>@<server> "sudo env ROLLBACK_ALLOW_ENV='PYTHONUNBUFFERED PYTHONDONTWRITEBYTECODE' bash -s" \
  < backend-dawn/deploy/rollback-to-fastapi.sh
```

`sudo env ...` 这个写法不能省：`sudo` 默认 `env_reset`，`ROLLBACK_ALLOW_ENV=x sudo ...` 传不进去。

规则：

- 空格分隔的**完整变量名**列表。不是前缀，不是子串，也不是 glob——
  `ROLLBACK_ALLOW_ENV="PYTHONUNBUFFERED"` 放不掉 `PYTHONPATH`。想放两个就写两个。
- 没列上的变量照旧 fail closed，脚本停在核验那一步、不切流。
- 每放行一个名字，脚本都会往日志里印一行 `[escape] ... 放行了加载器环境变量 <名字>`。
  放行必须留痕，回滚记录里要能看出你当时接受了什么。
- 不设它 = 今天的行为，一个字节不差。

**它是应急出口，不是配置项**：别把它写进 systemd 单元、`.env` 或任何常驻的地方。这个阀存在的
理由只有一个——宽匹配加 fail-closed 卡在应急通道上而又没有出口时，压力下的人会把整个检查
注释掉，那才是真正的失效模式。逃生阀把「静默被挡」换成「有意识地接受并留痕」。放行的判断
永远是人做的：`LD_PRELOAD` / `LD_LIBRARY_PATH` / `PYTHONPATH` / `PYTHONHOME` 出现在这个列表里，
基本等于承认「我不知道这个进程在跑什么代码」，那时候该做的是去看那一行是谁加的，不是放行。

回切脚本是同一套纪律的镜像：动 nginx 之前先核验 Dawn 运行时并跑完整探活（不是 curl 一个
`/health` 就算数——`/api/health` 不碰库、不碰七牛、不碰鉴权，它绿着的时候后端可以坏到任何
程度），reload **之后再核验一遍**，任何一步不过就还原 nginx、留在 FastAPI 上，并且
**重新完整核验一遍 FastAPI**（这条链里没有任何「刚才验过了」的缓存结论）。

### 手工核对库身份

脚本比的就是这两个值，你可以自己敲（在服务器上；`.rollback-probe` 是 root 的 600 文件，
`sudo` 不能省，非 root 读不到秘密时头是空的，探针只会回 404，看起来像「探针没配」）：

```bash
# 库文件的指纹
printf '%s' "$(stat -Lc '%d:%i' -- /opt/dawnop/data/dawnop.db)" | sha256sum
# 进程闩住的指纹（回滚期间 FastAPI 在跑时）
curl -s -H "X-Rollback-Probe: $(sudo cat /opt/dawnop/backend/.rollback-probe)" \
  http://127.0.0.1:8000/api/rollback/db-identity
```
两者必须相等。不等 = FastAPI 连的不是生产库（最常见原因还是 `.env` 里的相对
`DATABASE_URL`）。`$(...)` 那层不能省：直接 `stat ... | sha256sum` 会把尾换行一起算进去。

配方（`sha256("<st_dev>:<st_ino>")`）的唯一定义点在
`backend/app/core/db_identity.py`，`backend/tests/test_rollback_chain.py` 拿真文件把它和
脚本里的那个函数钉在一起——否则这一节的命令随时可能变成假的。
`backend/scripts/rollback_chain_mutants.py` 是这些断言的负控（会临时改生产文件，不进 CI）。

---

## 五、库备份

`/opt/dawnop/data/dawnop.db` 每天存一份压缩快照到 `/opt/dawnop/backups`，保留 14 份。
脚本 [`backup-db.sh`](./backup-db.sh) 调站点的 `--backup <dest>`，默认运行正式 JVM jar
及旁边完整 `lib/`，不依赖系统 SQLite CLI。维护命令对实际引擎执行 >=3.51.3 版本门，
用 `VACUUM INTO` 取一致快照，再由同一连接 attach 副本，检查完整的
`PRAGMA integrity_check` 结果与所有 FTS5 的 `integrity-check`（rank=1，同时验证外部正文）。
任何失败都不压缩、不发布、不轮转；不运行 migrate，不修改源库日志模式。

判词在 `backend/tests/test_backup_db.py`，同一组真库判词运行 JVM/native 两臂；
负控在 `scripts/backup_db_mutants.py`，只改带哨兵的隔离副本。
方案比较见 [`backup-engine-design.md`](../docs/backup-engine-design.md)。

### 安装或更新（由协调者在获批后执行）

先确认目标 main 完整 SHA 的 CI 全绿，并安装该版本的正式 jar 与完整依赖闭包；
旧 jar 没有 `--backup`，只替换脚本无法工作。JRE 21、gzip、sha256sum 必须可用。
**无需安装 sqlite3 CLI**。不要为备份升级或替换系统共享库。

```bash
# 更新期间暂停计划任务，并确认已有 oneshot 已退出；不杀正在产出快照的进程。
sudo systemctl stop dawnop-backup.timer
systemctl is-active dawnop-backup.service  # 应为 inactive

sudo mkdir -p /opt/dawnop/backups
sudo chown dawnop:dawnop /opt/dawnop/backups
sudo chmod 700 /opt/dawnop/backups
sudo install -m 755 -o root -g root deploy/backup-db.sh /opt/dawnop/backup-db.sh
sudo install -m 644 -o root -g root deploy/backup-engine.conf /opt/dawnop/backup-engine.conf
sudo install -m 644 -o root -g root deploy/dawnop-backup.service deploy/dawnop-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

公开配置 [`backup-engine.conf`](./backup-engine.conf) 由 systemd `EnvironmentFile` 读取，
包含 `BACKUP_ENGINE=jvm`、`BACKUP_JAR=/opt/dawnop-dawn/backend-dawn.jar`、JRE21 的
`JAVA_HOME`/`PATH` 与 tokenizer 路径，不放任何密钥。脚本用 argv 数组执行引擎，
不接受一整串 shell 命令。单元仍以 dawnop 运行，只允许库目录及备份目录写入；
jar、lib/ 和扩展只读。`PrivateTmp` 允许 JDBC 解包内嵌引擎。
安装前以 dawnop 身份核对 JRE 可执行、jar/lib/ 与扩展可读、所有父目录可遍历。

S13 验收通过后，才可把 `BACKUP_ENGINE` 改为 `native`，其
`BACKUP_NATIVE=/opt/dawnop-dawn/native/current/dawnop-native` 必须来自已验收的私有引擎包，
不能指向随便链接系统 SQLite 的 ELF。同时把 `DAWN_SIMPLE_EXT` 改为
`/opt/dawnop-dawn/native/current/extensions/libsimple.so`。native 包 constructor 的来源/版本门与 core
版本门都必须保留；不配置全局 `LD_LIBRARY_PATH`。切换后重跑下面手动自检，记录配置
偏离仓库默认 JVM 的原因。更改该文件后下一次 oneshot 直接读取，无需重启后端。

### 自检

```bash
sudo -u dawnop /usr/lib/jvm/java-21-openjdk-amd64/bin/java -jar /opt/dawnop-dawn/backend-dawn.jar --engine-info
# 应是固定 JDBC 实际版本与 source id，且不打开业务库。
sudo systemctl start dawnop-backup
journalctl -u dawnop-backup -n 30 --no-pager
# 必须出现 database backup verified 和 backup-db: 写入...；退出状态必须成功。
systemctl show dawnop-backup -p Result -p ExecMainStatus
sudo -u dawnop sh -c 'cd /opt/dawnop/backups && sha256sum -c ./*.sha256 && gzip -t ./*.db.gz'
sudo systemctl enable --now dawnop-backup.timer
systemctl list-timers dawnop-backup.timer dawnop-paste-cleanup.timer dawnop-native-cleanup.timer
```

库目录为 WAL/SHM 仍需可写；不能因“备份只读”把它改成只读。定时为本地 00:20，
再抖动 0..5 分钟；JVM/native cleanup 都是整点 + 0..60 秒，正常计划无冲突。
重启后 Persistent 补跑或超长任务仍可能相遇，修复版引擎与 SQLite 锁负责并发安全。
`check_server_drift.py` 同时核对脚本、service、timer 与公开引擎配置；它需要服务器
访问，只由协调者在获批后运行，本任务不运行。

### 恢复

恢复须另行获批。先在私有临时目录解压并用**同一修复版引擎**产生已完整验证的副本：

```bash
sudo -u dawnop mkdir -m 700 /opt/dawnop/backups/restore-check
sudo -u dawnop sh -c 'gzip -dc /opt/dawnop/backups/dawnop-<UTC时间戳>.db.gz > /opt/dawnop/backups/restore-check/input.db'
sudo -u dawnop env DAWNOP_ENV=/dev/null DAWN_DB_PATH=/opt/dawnop/backups/restore-check/input.db DAWN_SIMPLE_EXT=/opt/dawnop/backend/extensions/libsimple.so \
  /usr/lib/jvm/java-21-openjdk-amd64/bin/java -jar /opt/dawnop-dawn/backend-dawn.jar --backup /opt/dawnop/backups/restore-check/verified.db
```

确认命令成功及摘要后，停备份 timer、两个 cleanup timer，停实际启用的 JVM 服务或
native socket/实例，确认没有打开者；保留当前 DB/WAL/SHM 整套事故现场，再换入
`verified.db` 并设置 dawnop 属主。**不能**把旧 WAL/SHM 留在恢复库旁边，也不能在
仍有连接时删除它们。恢复后显式执行当前版本要求的 prepare/migrate，验证 health、
文章及两种 FTS；按先前记录只恢复一种 cleanup 执行者和备份 timer。完整恢复演练
尚未在生产执行，不能把这里的步骤当成已验收结果。

### 保留策略

每天本地 00:20–00:25 一份，留最近 14 份。文件名为 UTC，字典序即时间序。
份数在 service 的 `Environment=KEEP=`；脚本支持 `DB`、`DEST`、`KEEP` 或同名 CLI 参数。
轮转只删完整匹配 `dawnop-<UTC时间戳>.db.gz` 的文件与对应 `.sha256`，外来文件不动。
KEEP 必须至少为 1；单引号与空格路径按 SQL 参数绑定，不经点命令解析。

完成全客户端盘点后，若没有其他管理员工具依赖，可以另行卸载 `sqlite3` **CLI 包**，
以免旧引擎再碰共享库；卸载不是本任务动作，也不代表系统所有 SQLite 客户端已整改。
保留系统 `libsqlite3-0`，不做 autoremove；native 仍使用包内固定私有库，JVM 使用固定 JDBC。

### 这个方案没覆盖的

- **只防误删误改，不防机器丢失。** 备份和库在同一块盘上。盘坏了、服务器没了、
  文件系统烂了，两份一起没。异地或离线副本是待办，不在这一批里。
- **`.env` 不在备份范围内。** 七牛 AK/SK、`SECRET_KEY`、腾讯云密钥都在
  `/opt/dawnop/backend/.env`，那份丢了库还原出来也登不上、连不上对象存储。
  它现在只存在于服务器和本地私有记录里。
- **七牛上的文件本体不在备份范围内。** 库里存的是 path 到 key 的映射，
  对象在七牛私有空间。库还原到旧时点后，之后新传的对象会变成没有元数据的孤儿，
  用 `backend/scripts/sweep_qiniu_orphans.py` 清。
- **没有恢复演练。** 上面那套恢复步骤是写出来的，没在生产上走过一遍。
  第一次跑它的时候就是出事的时候，这一点和回滚脚本一样（见「四、回滚安全链」）。
