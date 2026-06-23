# 部署指南

把 dawnop-site 部署到一台 Ubuntu 服务器：Nginx 监听 80，托管前端静态产物并把
`/api` 反代到本机 uvicorn；uvicorn 由 systemd 守护。下文用占位符，按实际替换：

- `<user>`：登录服务器的用户名（需有 sudo）
- `<server>`：服务器地址（IP 或域名）
- `dawnop.com`：你的站点域名

> 约定目录：后端 `/opt/dawnop/backend`，前端产物 `/var/www/dawnop/dist`。
> 改目录的话，`deploy/dawnop-backend.service` 与 `deploy/nginx.conf` 里的路径要同步改。

---

## 一、首次部署

### 0. 本地构建前端
```bash
cd frontend
npm install
npm run build          # 产出 dist/（含 gzip-9 预压缩的 .gz 文件）
```

### 1. 服务器装依赖、建目录
```bash
ssh <user>@<server>
sudo apt-get update
sudo apt-get install -y nginx rsync python3-venv python3-pip
sudo mkdir -p /opt/dawnop/backend /var/www/dawnop/dist
sudo chown -R <user>:<user> /opt/dawnop /var/www/dawnop
exit
```

### 2. 传代码与产物（本地执行）
```bash
# 后端（排除虚拟环境/缓存/本地库）
rsync -az --delete \
  --exclude='.venv' --exclude='__pycache__' --exclude='*.db' --exclude='.pytest_cache' \
  backend/ <user>@<server>:/opt/dawnop/backend/

# 前端产物（含 .gz）
rsync -az --delete frontend/dist/ <user>@<server>:/var/www/dawnop/dist/
```

### 3. 准备后端 .env（服务器上，**不进仓库**）
以 `backend/.env.example` 为模板，填入七牛密钥、管理员账号等。要点：
```bash
cd /opt/dawnop/backend
# 生成生产强随机密钥（不要用示例里的弱默认值）
python3 -c "import secrets;print('SECRET_KEY='+secrets.token_urlsafe(48))"
# 把上面输出写入 .env 的 SECRET_KEY；CORS_ORIGINS 设为站点域名（同源反代其实可不依赖 CORS）
chmod 600 .env       # 含敏感信息，收紧权限
```
> 同源部署（Nginx 反代 `/api`）下浏览器不跨域，CORS 主要给「其他客户端直连」留。
> 七牛**文本预览/下载**仍需在七牛侧配 CORS + 绑自定义 HTTPS 域名（见七牛文档）。

### 4. 建虚拟环境、装依赖
```bash
cd /opt/dawnop/backend
python3 -m venv .venv
# ⚠️ 服务器在国内时 pip 直连 PyPI 会失败，务必用国内镜像
./.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip
./.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
# 冒烟：能 import 即可
./.venv/bin/python -c "from app.main import app; print('ok', len(app.routes))"
```

### 5. 初始化管理员
```bash
cd /opt/dawnop/backend
./.venv/bin/python scripts/seed_admin.py   # 读 .env 的 ADMIN_USERNAME/PASSWORD，并建默认页面
```

### 6. systemd 守护后端
```bash
sudo cp /opt/dawnop/backend/deploy/dawnop-backend.service /etc/systemd/system/dawnop-backend.service
# 若改过路径/用户，编辑该文件的 User / WorkingDirectory / ExecStart
sudo systemctl daemon-reload
sudo systemctl enable --now dawnop-backend
sudo systemctl is-active dawnop-backend          # 期望 active
curl -s http://127.0.0.1:8000/api/health         # 期望 {"status":"ok",...}
journalctl -u dawnop-backend -f                  # 排错时跟踪日志
```

### 7. Nginx 配置
```bash
# 站点配置（含 SPA history 回退 + /api 反代 + 静态长缓存）
sudo cp /opt/dawnop/backend/deploy/nginx.conf /etc/nginx/sites-available/dawnop
# 按需改 server_name 与 root
sudo ln -sf /etc/nginx/sites-available/dawnop /etc/nginx/sites-enabled/dawnop
sudo rm -f /etc/nginx/sites-enabled/default

# gzip 压缩（gzip_static 直接发预压缩 .gz；注意主 nginx.conf 已有 gzip on，勿重复）
sudo cp /opt/dawnop/backend/deploy/gzip.conf /etc/nginx/conf.d/gzip.conf

sudo nginx -t && sudo systemctl reload nginx
```
> `deploy/*` 之所以能在服务器上 `cp`，是因为第 2 步把整个 `backend/` 传上去了；
> 也可改成从本地 `scp deploy/xxx <user>@<server>:/tmp/` 再 `sudo mv`。

### 8. 验证
```bash
# 服务器本机
curl -s http://127.0.0.1/api/health
curl -s http://127.0.0.1/ | grep -o '<title>[^<]*</title>'
# 公网
curl -s http://dawnop.com/api/health
curl -sI http://dawnop.com/assets/<某个js> -H 'Accept-Encoding: gzip' | grep -i content-encoding  # 期望 gzip
```
浏览器打开站点：首页、文章页（公式/代码高亮）、`/admin/login` 登录、文件管理。

---

## 二、日常更新（再次发版）

**前端改动**：
```bash
cd frontend && npm run build
rsync -az --delete frontend/dist/ <user>@<server>:/var/www/dawnop/dist/
# 静态资源带哈希、index.html 不缓存，发完即时生效，无需重启/刷缓存
```

**后端改动**：
```bash
rsync -az --delete \
  --exclude='.venv' --exclude='__pycache__' --exclude='*.db' --exclude='.pytest_cache' \
  backend/ <user>@<server>:/opt/dawnop/backend/
ssh <user>@<server> 'sudo systemctl restart dawnop-backend'
# 若改了 requirements.txt：先在服务器上 ./.venv/bin/pip install -i <镜像> -r requirements.txt 再重启
```

**改了 Nginx/gzip 配置**：`sudo nginx -t && sudo systemctl reload nginx`。

---

## 三、注意事项

- **数据库**：SQLite 在 `/opt/dawnop/backend/dawnop.db`，**不在** rsync 范围（已 exclude），更新代码不会动它。改了 ORM 模型结构需重建库（删库后重跑 `seed_admin.py`，本项目无迁移）。记得定期备份这个文件。
- **密钥/账号**：只存服务器上的 `.env`（权限 600），不要进仓库；仓库只有 `.env.example`。
- **HTTPS（建议尽快上）**：绑定域名后用 certbot 签证书，加 443 server 块并把 80 跳转到 443，同时开 `http2`。站点上 HTTPS 后，七牛私有空间也需绑自定义 HTTPS 域名，否则混合内容被拦。
- **CDN（可选，国内需备案）**：静态资源走 CDN 回源可降低异地延迟，方案见项目记录；前端 `vite.config.js` 设 `base` 指向 CDN 域名、`/assets` 加 CORS 头即可，部署流程不变。
- **端口**：uvicorn 只监听 `127.0.0.1:8000`（不对外），对外只暴露 Nginx 的 80/443。
