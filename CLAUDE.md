# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 本仓库目前为空（仅有 `program.md` 需求说明），尚无代码。下面是基于 `program.md`
> 制定的完整开发计划与约定。每完成一个阶段，请回来更新本文件中的「常用命令」「目录结构」
> 等小节，使其与实际代码保持一致。

## 1. 项目目标（来自 program.md）

`dawnop-site` 是一个**风格简洁、干净的个人博客网站**，同时内置一个**后台网盘 / 文件管理**模块。

- **博客前台**：首页、文章页、关于页。文章页需渲染 Markdown 并支持**内嵌 LaTeX**。
- **后台（需登录）**：
  - 文章管理：增删改查、Markdown 文件的上传 / 下载。
  - 文件管理：图片与文本的上传 / 下载，且图片、文本要支持**在线预览**；文件实际存储在
    **七牛云 Kodo 对象存储**（API 文档：https://developer.qiniu.com/kodo/3939/overview-of-the-api）。
- **前后端分离**：后台网盘后续可能要支持其他客户端，因此后端以纯 API 提供服务。
- **后端尽量用 Python**（求简单），数据库用 **SQLite** 即可。

## 2. 技术选型

| 层 | 选型 | 理由 |
|----|------|------|
| 后端框架 | **FastAPI** + Uvicorn | Python、轻量、自带 OpenAPI 文档（利于后续多客户端对接） |
| ORM / DB | **SQLAlchemy 2.x** + **SQLite** | 需求明确指定 SQLite |
| 鉴权 | OAuth2 Password + **JWT**（python-jose、passlib[bcrypt]） | 前后端分离用 token 而非 session |
| 对象存储 | 七牛云 **Kodo**，官方 `qiniu` Python SDK | 需求指定 |
| 前端框架 | **Vue 3 + Vite** | 简单、构建快、契合「简洁干净」 |
| Markdown | `markdown-it` | 可扩展插件体系 |
| LaTeX | **KaTeX**（`markdown-it-katex` / `@vscode/markdown-it-katex`） | 渲染快，按需加载 |
| 代码高亮 | `highlight.js` | 文章代码块 |
| 部署 | **Nginx**（80 端口）+ systemd 托管 uvicorn | 4 核 4G 足够 |

> 这些是推荐默认值。若你（用户）更倾向 Flask / React 等，请在动工前提出，我会相应调整计划。

## 3. 规划目录结构

```
dawnop-site/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口、路由挂载、CORS
│   │   ├── config.py            # 读取 .env（七牛密钥、JWT secret 等）
│   │   ├── database.py          # SQLAlchemy engine / session
│   │   ├── models/              # ORM 模型：User, Article, FileObject
│   │   ├── schemas/             # Pydantic 请求/响应模型
│   │   ├── api/
│   │   │   ├── auth.py          # 登录、获取 token
│   │   │   ├── articles.py      # 文章 CRUD + md 上传/下载
│   │   │   └── files.py         # 七牛上传/下载/列表/预览
│   │   ├── core/
│   │   │   ├── security.py      # 密码哈希、JWT 签发/校验
│   │   │   └── qiniu_client.py  # 七牛 SDK 封装（签 token、上传、下载 url）
│   │   └── deps.py              # 依赖项（当前用户、DB session）
│   ├── tests/                   # pytest
│   ├── requirements.txt
│   ├── .env.example             # 模板，提交；真实 .env 不提交
│   └── scripts/seed_admin.py    # 初始化管理员账号
├── frontend/
│   ├── src/
│   │   ├── views/               # Home, Article, About, Login, AdminArticles, AdminFiles
│   │   ├── components/          # MarkdownView（md+katex）、FilePreview 等
│   │   ├── api/                 # axios 封装，统一带 token
│   │   ├── router/              # 公开路由 + 后台受保护路由
│   │   └── store/               # 登录态
│   ├── package.json
│   └── vite.config.js
├── deploy/
│   ├── nginx.conf               # 静态前端 + /api 反代
│   └── dawnop-backend.service   # systemd 单元
├── .gitignore                   # 必含 .env、*.db、node_modules、__pycache__、dist
├── CLAUDE.md
├── README.md
└── program.md
```

## 4. 数据模型（初版）

- **User**：`id, username, password_hash, created_at`（单管理员，启动脚本注入）。
- **Article**：`id, title, slug(唯一), summary, content(markdown), published(bool), created_at, updated_at`。
- **FileObject**：`id, key(七牛 key, 唯一), filename, content_type, size, created_at`。
  本地只存元数据用于列表/预览，文件本体在七牛。

## 5. 关键 API 草图

- `POST /api/auth/login` → 返回 JWT。
- 文章：`GET /api/articles`（公开，分页）、`GET /api/articles/{slug}`（公开）、
  `POST/PUT/DELETE /api/articles`（需鉴权）、`POST /api/articles/import`（上传 .md）、
  `GET /api/articles/{id}/export`（下载 .md）。
- 文件：`GET /api/files`（列表）、`POST /api/files/upload-token`（向前端下发七牛上传凭证，
  前端直传七牛）或 `POST /api/files/upload`（经后端代理上传）、`GET /api/files/{key}/url`
  （生成下载/预览私有 url）、`DELETE /api/files/{key}`。图片/文本预览由前端按 content_type 渲染。

> 直传 vs 代理上传：默认推荐**前端直传**（后端只签 token），减轻 4G 机器带宽/内存压力；
> 若需服务端校验/改名，再走代理上传。

## 6. 开发阶段计划（建议按序推进，每阶段本地测试通过再进入下一阶段）

- **阶段 0 — 工程初始化**
  - `git init`；写 `.gitignore`、`README.md`、`backend/.env.example`、`frontend/` 脚手架。
  - 提交时**不要**带 Claude 作为 co-author（见第 7 节）。
- **阶段 1 — 后端骨架**：FastAPI 启动、`config.py` 读 `.env`、SQLite 连接、CORS、健康检查。
- **阶段 2 — 鉴权**：User 模型、密码哈希、JWT 登录、`seed_admin.py`、受保护依赖。
- **阶段 3 — 文章模块**：Article 模型、CRUD、md 导入/导出、公开只读接口 + pytest。
- **阶段 4 — 七牛文件模块**：`qiniu_client.py`、上传凭证 / 上传、列表、预览/下载 url、删除。
- **阶段 5 — 前端公开页**：首页、文章页（markdown-it + KaTeX + highlight.js）、关于页，简洁主题。
- **阶段 6 — 前端后台**：登录页、文章管理、文件管理（图片/文本预览），axios 统一带 token。
- **阶段 7 — 本地联调测试**：前后端跑通全流程，跑测试。
- **阶段 8 — 部署上线**：服务器 Nginx + systemd，绑定 `dawnop.com`，80 端口验证。

## 7. 重要约定（务必遵守）

- **Git co-author**：提交时**绝不**添加 `Co-Authored-By: Claude`。本项目以开源为标准。
- **敏感信息**：七牛 AccessKey/SecretKey、Bucket、JWT secret、管理员密码等一律放
  `backend/.env`（**不提交**），仓库只提交 `.env.example` 模板。`.gitignore` 必须涵盖 `.env`、`*.db`。
- **环境流程**：先**本地开发 + 本地测试**，无误后再上服务器测试，不要直接在生产改。
- **前后端分离**：后端只提供 JSON API，不渲染页面；为后续其他客户端保持接口稳定、文档清晰
  （FastAPI 自带 `/docs`）。

## 8. 部署目标环境

- 机器：4 核 4G；登录：`ssh <user>@<server>`；**80 端口已开放**。
- 验证：可用公网域名 `dawnop.com` 测试。
- 形态：Nginx 监听 80，托管 `frontend/dist` 静态文件，并将 `/api` 反向代理到本地 uvicorn；
  uvicorn 由 systemd（`deploy/dawnop-backend.service`）守护。

## 9. 常用命令（脚手架建立后补全到此）

```bash
# 后端（建立后）
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/seed_admin.py            # 初始化管理员
uvicorn app.main:app --reload           # 本地开发, http://127.0.0.1:8000, 文档 /docs
pytest                                   # 全部测试
pytest tests/test_articles.py::test_x   # 单个测试

# 前端（建立后）
cd frontend && npm install
npm run dev                              # Vite 开发服务器
npm run build                            # 产出 dist/
```
