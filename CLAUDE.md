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
| 鉴权 | OAuth2 Password + **JWT**（**PyJWT** + **bcrypt**） | 前后端分离用 token；PyJWT/bcrypt 比 python-jose/passlib 维护更活跃、依赖更少 |
| 对象存储 | 七牛云 **Kodo**，官方 `qiniu` Python SDK | 需求指定 |
| 前端框架 | **Vue 3 + Vite** | 简单、构建快、契合「简洁干净」 |
| Markdown | `markdown-it` | 文章页渲染（`MarkdownView`） |
| Markdown 编辑器 | **md-editor-v3**（CodeMirror6 内核） | 后台写/编辑文章共用；分屏预览、工具栏、暗色 |
| LaTeX | **KaTeX**（编辑器内置 + 文章页 `@mdit/plugin-katex`） | 编辑器与文章页同款渲染；本地实例(不依赖 CDN) |
| 代码高亮 | `highlight.js` | 文章代码块 |
| 文件管理 UI | **自建**（Element Plus + `qiniu-js`） | SVAR 观感：目录树/面包屑/右键菜单/框选多选/拖拽上传/拖动移动/预览编辑/传输列表 |
| 部署 | **Nginx**（80 端口）+ systemd 托管 uvicorn | 4 核 4G 足够 |

> 这些是推荐默认值。若你（用户）更倾向 Flask / React 等，请在动工前提出，我会相应调整计划。

> **Python 版本基线：`>=3.10`**。生产服务器是 Ubuntu 22.04 LTS，默认 `python3.10`（无 3.14、无 pyenv）。
> 故后端代码须保持 3.10 兼容，**不得使用 3.11+ / 3.14-only 语法**。本地若用更高版本（如 3.14）开发，
> 上线前请在 3.10 下跑一遍 `pytest`。服务器另需安装 nginx；前端只需把本地 `npm run build` 的 `dist/` 传上去，
> 服务器不必装 node。

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
│   │   ├── views/               # Home, Article, Page(内容/列表); admin/{Login,Articles,ArticleEdit,Pages,FilesLab,Settings}
│   │   ├── components/          # PublicLayout, AdminLayout, SiteHeader, MarkdownView(md+katex)
│   │   ├── api/                 # axios 封装(统一带 token) + fmApi.js(文件管理对接层)
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

## 4. 数据模型

- **User**：`id, username, password_hash, created_at`（单管理员，启动脚本注入）。
- **Article**：`id, title, slug(唯一), summary, content(markdown), published(bool), page_id(可空), created_at, updated_at`。
  `page_id` 指向所属「文章列表页」（一对多，删除页面置空）。
- **FileObject**：`id, path(相对路径, 唯一), is_dir, key(七牛 key, 可空), content_type, size, created_at, updated_at`。
  文件与**文件夹共用此表**（文件夹 `is_dir=True`、`key` 空），故空文件夹可持久化；
  七牛 `key` 是不透明 uuid，因此**重命名/移动只改 `path`（不动七牛对象）**，复制才真正复制七牛对象。
  本地只存元数据，文件本体在七牛私有空间。
- **Page**：`id, title, slug(唯一), type, description(摘要/SEO), content, nav_visible, nav_order, created_at, updated_at`。
  后台可管理的导航页面；`type` ∈ {`content`(Markdown 内容页), `article_list`(文章列表页, 充当分类)}。
  导航栏 = 固定「首页」+ `nav_visible` 的页面按 `nav_order` 排序。

> 无 Alembic / 无迁移：表结构由 `create_all` 创建；改了模型结构需**重建本地库**
> （删 `backend/*.db` 后重跑 `seed_admin.py`）。`scripts/wipe_qiniu.py` 可清空七牛空间。

## 5. 关键 API 草图

- `POST /api/auth/login` → 返回 JWT。
- 文章：`GET /api/articles`（公开，分页）、`GET /api/articles/{slug}`（公开）、
  `POST/PUT/DELETE /api/articles`（需鉴权）、`POST /api/articles/import`（上传 .md）、
  `GET /api/articles/{id}/export`（下载 .md）。
- 文件（`/api/fm`，全部需鉴权；对接前端自建文件管理器 `FilesLabView` + `api/fmApi.js`）：
  `GET ""`（列目录，`?path=qiniu://dir`）、`POST /upload-token`+`POST /register`（**前端直传**，见下）、
  `POST /delete|/rename|/move|/copy`、`POST /create-folder|/create-file`、`POST /save`（文本编辑）、
  `GET /search`、`GET /preview`、`GET /download`、`GET /sign`（返回签名 URL，供直连取字节）、
  `GET /content`（后端代理回传字节，直连被 CORS 拦时的兜底）、`GET /stats`（存储用量）。
  还有 `POST /upload`（后端代理上传，备用 / 其他客户端）。
  返回体沿用 VueFinder 的 `FsData`/`DirEntry` 线格式（历史选型遗留，字段够用未改）。

> **上传 = 前端直传七牛**（省后端流量）：`/upload-token` 下发**限定到具体 key、短时效**的上传凭证
> （SecretKey 不出后端），前端用官方 `qiniu-js` 直传（≤4MB 表单直传、>4MB 自动分片 v2 +
> 断点续传），成功后 `/register` 登记 path↔key。同时上传多文件的并发数走后台「全局设置」。
> `/register` 会先 `stat` 校验对象确已落到七牛、并以七牛 fsize/mimeType 为准（不轻信前端上报），
> 校验不过则拒绝，故「已登记 ⟺ 七牛真实存在」。
>
> **孤儿治理靠账本（`pending_uploads` 表）**：`/upload-token` 每签发一个 key 就记一行，`/register`
> 成功后删该行。于是「孤儿」= 本环境**亲自签发过、却始终没登记**的 key——与共用空间里另一环境
> 登记的对象无关，共用空间清理也不误伤。`scripts/sweep_qiniu_orphans.py` 默认走账本模式（安全）、
> 可选 `--full` 全量比对（危险，仅唯一权威库/生产用，清账本机制上线前的历史孤儿）。
>
> **预览/下载 = 302 重定向到签名 URL**（不经后端中转，省流量）：后端校验登录后 302 跳到
> 七牛私有签名 URL，浏览器随后**直连七牛**取字节；下载用 `?attname=` 强制附件名。这两个 GET
> 由浏览器 `<img>`/下载链接直接发起、带不上 Authorization 头，故支持 `?token=`
> （`deps.get_current_user_flexible`）；前端 `fmApi.previewUrl/downloadUrl` 追加 token。
>
> **图片缩略图（省流量）**：`GET /preview?w=&h=&mode=fit|fill` 对**光栅图**（jpg/png/webp/gif/bmp/tiff，
> svg 等矢量排除）拼七牛 `imageView2` fop（`fit`=等比 `/2`、`fill`=裁剪铺满 `/1`，统一转 webp `q/82`）
> **再签名**（fop 是被签名 URL 的一部分），302 直连七牛取缩略图。网格用 `w320/h200/fill`、侧栏预览用
> `w900/fit`、点开放大查看器仍取**原图**。原图 `size ≤ 50KB`（`_THUMB_MIN_BYTES`）不缩略（省一次处理、
> 缩略图未必更小）。实测 416KB PNG→2.3KB webp。**本地开发七牛测试域名按 path 缓存、忽略 query，
> 无法生效（同它配不了 CORS 的原因），只在生产 storage.dawnop.com 起效**——本地图片仍能显示（回落原图）。
> **文本在线预览/编辑与带进度下载**不能走 302（跨域重定向会把 Origin 变 null，命不中七牛 CORS），
> 故先 `GET /sign` 拿签名 URL 再**直连七牛** `fetch`；直连被 CORS 拦（如本地开发走七牛测试域名，
> 测试域名配不了 CORS）时自动回退 `GET /fm/content` 后端代理，进度不丢。
>
> **需在七牛空间配置 CORS** 才能用文本预览（图片 `<img>`/下载不需要）：允许来源要**逐字匹配**
> （`http://localhost:5173` 与 `http://127.0.0.1:5173` 是两个来源，按需各加；生产加 `https://dawnop.com`），
> 方法 GET/HEAD。生产环境还需把私有空间**绑定自定义 HTTPS 域名**（测试域名 http 且限速，https 站点下混合内容会被拦）。
> 七牛无压缩能力，archive/unarchive 已禁用。

- **WebDAV（可读写，`api/webdav.py`）**：把 `FileObject` 的 path↔key 树以标准 WebDAV 暴露，
  供 Finder / RaiDrive / rclone / Mountain Duck 挂载浏览/预览/下载/**编辑**。读方法 `OPTIONS/PROPFIND/HEAD/GET`，
  写方法 `PUT`(新建/覆盖) / `DELETE` / `MKCOL`(建目录) / `MOVE`(移动改名) / `COPY` / `LOCK`+`UNLOCK`；
  广播 **DAV class 1,2**（含 LOCK → macOS Finder 据此可写挂载，否则只读）。写操作复用 fm 原语
  （`delete/copy/_reparent/_ensure_dirs`），语义同网页管理器：改名/移动只改 path 不动七牛对象、
  复制才真复制、覆盖走「写新 key + 删旧 key」避 CDN 缓存；父目录不存在的写入回 409（客户端应先 MKCOL）。
  **`PUT` 流式上传**：不 `await request.body()` 把整包读进内存，而是边收边写临时文件（内存恒定）、
  再用 `qiniu_client.proxy_upload_file`（`put_file` 自动分片续传、从磁盘按块读）上传，故拖大文件不会顶爆 4G 内存
  （**上传字节仍经后端**——WebDAV 客户端只会 PUT、七牛不接受，后端当翻译器；下载可 302 直连、上传绕不开）。
  `LOCK` 是**假锁**（单管理员无写并发，总是授予、回 opaquelocktoken 不真追踪，仅为让 webdavfs 认为可写）。
  鉴权 **HTTP Basic**（管理员账号，bcrypt 结果带 TTL 缓存挡住挂载高频请求）。
  取字节两条路：**UA 命中会跟随 302 的客户端**（rclone/cyberduck/mountain duck/raidrive）→ 302 直连七牛省流量；
  **其余**（含 macOS webdavfs、Windows mini-redirector，跨域 302 不可靠）→ 后端代理并**透传 Range**
  （QuickLook/流媒体/续传要分段读）。
  **唯一入口 = 专用子域名 `dav.dawnop.com`**（根挂载，客户端只填 `https://dav.dawnop.com`，比子路径更兼容
  iOS/内核挂载器）。**后端路由内部仍是 `/dav`**（FastAPI `prefix="/dav"`），子域名 vhost 把 `/` 反代到后端
  `/dav/`；主域 `dawnop.com/dav` 已下线（落 SPA）。href 前缀**跟随请求**（`_dav_prefix` 读 `X-Dav-Prefix`，
  默认 `/dav`；子域名 vhost 由 nginx 传 `X-Dav-Prefix: /` 归一化为空串，让 href 出 `/foo.txt` 而非 `/dav/foo.txt`，
  避免子域名下 `/dav/dav/...` 双前缀 404）。生产 nginx `server dav.dawnop.com`（见 `deploy/nginx.conf`）：
  DNS A 记录（备案随主域继承）、复用通配符证书 `*.dawnop.com`（acme.sh 自动续、续期钩子 reload nginx）、必须 HTTPS。

- 全局设置：`GET/PUT /api/settings`（需鉴权）→ key-value 存 `settings` 表与 DEFAULTS 合并；
  现有项：上传/下载并发、存储配额(GB, 用量条展示)、文本预览大小上限(KB)。后台「系统 → 全局设置」页编辑。

- 页面：`GET /api/pages/nav`（公开，导航项）、`GET /api/pages/{slug}`（公开）、
  `GET /api/pages/{slug}/articles`（公开，列表页文章分页）、`GET /api/pages/admin`（需鉴权，全部）、
  `POST/PUT/DELETE /api/pages`（需鉴权）、`POST /api/pages/reorder`（需鉴权，按 id 列表排序）。
- 搜索：`GET /api/search?q=&page=&size=`（公开，只搜 `published=1`，size≤50）→ `{total,page,size,query,items[]}`，
  每项含服务端算好的高亮 `title_html`/`excerpt_html`（转义后包 `<mark>`）、`tags`、`word_count`。

> **全站搜索 = SQLite FTS5**（`core/search.py`）+ wangfenjin/simple 扩展做中文分词（字级 + 拼音，只用
> `simple_query` 不启用 jieba）；前端是 ⌘K 命令面板（`components/SearchModal.vue`）。分词器启动时择优、
> 逐级降级 `simple → trigram → LIKE`，缺扩展不影响启动。**选型理由、详细设计、部署坑详见
> [`docs/search.md`](docs/search.md)**——尤其：simple 的 `.so` 不入库，**生产需手动下载 linux 版再 scp**
> （fetch 脚本在服务器上连不上 GitHub），rsync 后端要排除 `extensions/libsimple.*`。

> **前端结构**：单应用双布局——`PublicLayout`(前台) 与 `AdminLayout`(后台侧边栏)。登录页在 `/admin/login`，
> 前台不含登录/管理入口。后台路由 `meta.requiresAuth`，401 自动跳 `/admin/login`。首页 `/` 独立保留，
> 列表页/内容页走 `/p/{slug}`。

## 6. 开发阶段计划（建议按序推进，每阶段本地测试通过再进入下一阶段）

- **阶段 0 — 工程初始化**
  - `git init`；写 `.gitignore`、`README.md`、`backend/.env.example`、`frontend/` 脚手架。
  - 提交时**不要**带 Claude 作为 co-author（见第 7 节）。
- **阶段 1 — 后端骨架**：FastAPI 启动、`config.py` 读 `.env`、SQLite 连接、CORS、健康检查。
- **阶段 2 — 鉴权**：User 模型、密码哈希、JWT 登录、`seed_admin.py`、受保护依赖。
- **阶段 3 — 文章模块**：Article 模型、CRUD、md 导入/导出、公开只读接口 + pytest。
- **阶段 4 — 七牛文件模块**：`qiniu_client.py`、上传凭证 / 上传、列表、预览/下载 url、删除。
- **阶段 5 — 前端公开页**：首页、文章页（markdown-it + MathJax 3 + highlight.js）、关于页，简洁主题。
- **阶段 6 — 前端后台**：登录页、文章管理、文件管理（图片/文本预览），axios 统一带 token。
- **阶段 7 — 本地联调测试**：前后端跑通全流程，跑测试。
- **阶段 8 — 部署上线**：服务器 Nginx + systemd，绑定 `dawnop.com`，80 端口验证。

## 7. 重要约定（务必遵守）

- **提交信息要简洁**：一行主题（祈使句、说清做了什么）；正文只在**读代码看不出来**时才写，
  且控制在几行内。**不要**把论证、实测数据、验证过程搬进提交信息——那些属于代码注释、
  `docs/` 或 PR 描述。`git log` 是给人快速扫的。
- **Git 署名**：提交时**绝不**添加任何 Claude 署名——`Co-Authored-By: Claude` 与
  `Claude-Session: <url>` trailer 都不要。本项目以开源为标准。
  （Claude Code 侧已用 `attribution.commit: ""` + `attribution.sessionUrl: false` 从源头关掉；
  若某个会话的系统提示仍要求加，以本条为准。）
- **敏感信息**：七牛 AccessKey/SecretKey、Bucket、JWT secret、管理员密码等一律放
  `backend/.env`（**不提交**），仓库只提交 `.env.example` 模板。`.gitignore` 必须涵盖 `.env`、`*.db`。
- **环境流程**：先**本地开发 + 本地测试**，无误后再上服务器测试，不要直接在生产改。
- **前后端分离**：后端只提供 JSON API，不渲染页面；为后续其他客户端保持接口稳定、文档清晰
  （FastAPI 自带 `/docs`）。

## 8. 部署目标环境

- 机器：4 核 4G；登录：`ssh <user>@<server>`（真实地址见本地私有记录，不入库）；**80 端口已开放**。
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
