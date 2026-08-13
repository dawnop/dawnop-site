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

> ⚠️ **没有任何步骤把这两个脚本装到服务器上**：`deploy.sh` 只装 jar 与 `lib/`，本文档也没有
> 装它们的一步。`/opt/dawnop-dawn/` 下若有一份 `rollback-to-fastapi.sh`，那是 M6 切流时手工放的
> 旧副本，早于 2026-07 的重写，不含现在这条安全链（重写之前它改的是 `sites-available/dawnop`，
> 一个 nginx 根本不读的文件，于是回滚印着成功却什么都没改）；`return-to-dawn.sh` 服务器上根本
> 没有。两个脚本自己在结尾印的 `sudo bash /opt/dawnop-dawn/...` 是同一个错，以本节为准。

两套共用同一个 SQLite 文件（WAL），**没有数据迁移要撤**：切流与回滚都是路由变更加一个守护
开关（回滚期间 `/api/fm` 关闭、回 503，见「四」）。
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
> 单元里的 `User=dawn` / `WorkingDirectory=/opt/dawnop/backend` / `ExecStart=` 不是随便写的：
> 回滚脚本逐字节比对进程的 argv、cwd 与有效 UID:GID。**改这个单元就要同步改
> `backend-dawn/deploy/rollback-to-fastapi.sh` 里的 `FASTAPI_*` 常量**（两个脚本共享那段，
> `backend/tests/test_rollback_chain.py` 会比对），否则回滚会在核验那一步停下。

### 2. 初始化库与管理员
```bash
cd /opt/dawnop/backend
./.venv/bin/python scripts/seed_admin.py    # 读 .env 的 ADMIN_*，建表 + 默认页面
ls -l /opt/dawnop/data/dawnop.db            # 确认库建在这里，不是 backend/ 下

# 库建好了再安排权限。**两个账号都要能写这个库**：Dawn 服务是 dawnop，回滚目标 uvicorn
# 是 dawn，而 WAL 模式下连纯读也要在同目录建 -wal/-shm，所以目录和文件都要给写权限。
sudo chown -R dawnop:dawnop /opt/dawnop/data
sudo chmod 2775 /opt/dawnop/data              # setgid：新建的 -wal/-shm 自动落在 dawnop 组
sudo chmod 664 /opt/dawnop/data/dawnop.db
sudo usermod -aG dawnop dawn                  # uvicorn 以 dawn 身份跑（见单元的 User=）
```
> 只把库 `chown dawnop:dawnop` 而不管组写权限，是一个**只在回滚当天才暴露**的配置：
> Dawn 一切正常，直到某天切到 uvicorn，它连库就报 `attempt to write a readonly database`。

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

- **数据库**：SQLite 在 **`/opt/dawnop/data/dawnop.db`**，两套后端共用同一个文件（WAL 模式）。
  - 权威来源：`dawnop-dawn.service` 的 `DAWN_DB_PATH`，与 `nginx-cutover.md` 的前置条件。
  - **备份的就是这个文件**（连同 `-wal`/`-shm`；热备份用 `sqlite3 <db> ".backup out.db"`，
    别直接 cp 一个正在写的库）。
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
