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
脚本 [`backup-db.sh`](./backup-db.sh)，定时器 [`dawnop-backup.timer`](./dawnop-backup.timer)。

判词在 `backend/tests/test_backup_db.py`（跑真脚本、真库），负控在
`scripts/backup_db_mutants.py`。

### 装
```bash
# 依赖：sqlite3 命令行工具。2026-08-14 实测这台机器上没有它（libsqlite3-0 有，CLI 没有），
# 装它会顺带把 libsqlite3-0 从 3.37.2-2ubuntu0.3 升到 0.7（同一安全序列内的点版本）。
# 服务器 sqlite 是 3.37.2；本地跑判词的那台是 3.53（linuxbrew），两者行为差异没有对拍过。
sudo apt-get install -y sqlite3

# 备份目录。/opt/dawnop 不归 dawnop，所以这一步只能 root 做，脚本自己造不出来
sudo mkdir -p /opt/dawnop/backups
sudo chown dawnop:dawnop /opt/dawnop/backups
sudo chmod 700 /opt/dawnop/backups

# 脚本（unit 的 ExecStart 指的就是这个路径）
sudo install -m 755 -o root -g root deploy/backup-db.sh /opt/dawnop/backup-db.sh

sudo cp deploy/dawnop-backup.service deploy/dawnop-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dawnop-backup.timer
```

`backup-db.sh` 不在任何 rsync 范围内（`一、日常更新` 只同步 `backend/` 与 `frontend/dist/`），
改了脚本要重跑上面那条 `install`。

### 验
```bash
# 手动跑一次，看它写了什么
sudo systemctl start dawnop-backup
journalctl -u dawnop-backup -n 20 --no-pager
# 期望有一行：backup-db: 写入 /opt/dawnop/backups/dawnop-<时间戳>.db.gz（N 字节），保留 14 份，删除旧备份 M 个

# 下次触发时间
systemctl list-timers dawnop-backup.timer

# 产物自己校验自己
cd /opt/dawnop/backups && sudo -u dawnop sha256sum -c ./*.sha256
```

### 恢复
```bash
# 1. 挑一份，解压到临时位置，先验它是好的
cd /tmp
sudo cp /opt/dawnop/backups/dawnop-20260814T000000Z.db.gz .
gunzip dawnop-20260814T000000Z.db.gz
sqlite3 dawnop-20260814T000000Z.db 'PRAGMA integrity_check;'   # 必须是 ok

# 2. 停后端。换文件前必须没有进程连着它
sudo systemctl stop dawnop-dawn

# 3. 留住现场再覆盖。现在这个坏库仍是唯一记录了「出事之后发生过什么」的东西
sudo mv /opt/dawnop/data/dawnop.db /opt/dawnop/data/dawnop.db.before-restore
# 这个库是 delete 模式，正常情况下不会有这两个文件；真有就是上次没停干净，一起清掉
sudo rm -f /opt/dawnop/data/dawnop.db-wal /opt/dawnop/data/dawnop.db-shm
sudo install -m 644 -o dawnop -g dawnop \
  /tmp/dawnop-20260814T000000Z.db /opt/dawnop/data/dawnop.db

# 4. 起
sudo systemctl start dawnop-dawn
curl -s http://127.0.0.1:8001/api/health
```

留下的 `-wal` / `-shm` 必须一起删。SQLite 会把它们当成新库的一部分回放，
结果既不是备份也不是原库。（这个库 `journal_mode=delete`，2026-08-14 实测，
所以这一步平时是空转；写在这里是因为模式是可以被改的，而改了以后没人会回来更新这份文档。）

### 保留策略

每天 00:00（本地时区，`RandomizedDelaySec=1800` 内抖动）一份，留最近 14 份。
文件名里的时间戳是 UTC，字典序即时间序。改份数改 `dawnop-backup.service` 的
`Environment=KEEP=`。

轮转只删 `dawnop-<UTC 时间戳>.db.gz` 这个模式的文件和它的 `.sha256`，
目录里放的别的东西不会被动。

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
- **两个 unit 文件本身没有自动化判词覆盖。** `scripts/check_server_drift.py` 只盯
  `dawnop-backend` 和 `dawnop-dawn` 两个单元，`dawnop-backup` 还没登记进去。
