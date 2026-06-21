# dawnop-site

简洁、干净的个人博客网站，内置需登录的后台文件管理（网盘）模块。

- **博客前台**：首页、文章页（Markdown + 内嵌 LaTeX）、关于页。
- **后台**（需登录）：文章管理（含 Markdown 上传/下载）、文件管理（图片/文本上传下载与在线预览，文件存于七牛云 Kodo）。
- **前后端分离**：后端为纯 JSON API（FastAPI），便于后续接入其他客户端。

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | FastAPI + Uvicorn |
| ORM / DB | SQLAlchemy 2.x + SQLite |
| 鉴权 | OAuth2 Password + JWT |
| 对象存储 | 七牛云 Kodo（`qiniu` SDK） |
| 前端 | Vue 3 + Vite |
| Markdown / LaTeX | markdown-it + KaTeX + highlight.js |
| 部署 | Nginx + systemd |

## 本地开发

### 后端

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 填入七牛密钥、JWT secret 等
python scripts/seed_admin.py  # 初始化管理员账号
uvicorn app.main:app --reload # http://127.0.0.1:8000  文档 /docs
```

### 前端

```bash
cd frontend
npm install
npm run dev   # Vite 开发服务器
npm run build # 产出 dist/
```

## 配置与安全

- 敏感信息（七牛 AccessKey/SecretKey、Bucket、JWT secret、管理员密码等）放在 `backend/.env`，**不提交**。
- 仓库仅提交 `backend/.env.example` 模板。

## 部署

见 `deploy/`：Nginx 监听 80 端口托管 `frontend/dist`，并将 `/api` 反代到本地 uvicorn；uvicorn 由 systemd 守护。

详细开发计划见 [CLAUDE.md](./CLAUDE.md)。
