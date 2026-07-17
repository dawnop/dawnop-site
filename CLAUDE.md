# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **状态：已上线并持续演进**（[dawnop.com](https://dawnop.com)）。本文件最初是动工前
> 基于 `program.md` 写的开发计划，第 1–8 节的技术选型与约定仍然有效，但第 6 节的
> 「阶段计划」是历史记录——那些阶段早已完成，此后还发生了本文件未预见的事：Element Plus
> 重构、全站搜索、WebDAV、可视化组件，以及最大的一件——**M6 把整个后端用自制语言
> [Dawn](https://github.com/dawnop/dawn-lang) 重写并全量切流**（见第 2 节的「后端」一行）。
>
> 改动结构后请回来更新「常用命令」「目录结构」，别让本文件重蹈覆辙：它曾在有 4 万行代码
> 的仓库里，开篇写着「本仓库目前为空」。

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
| 后端 | **Dawn**（自制语言 → JVM 字节码），版本由根目录 `.dawn-version` 钉住 | M6 用 strangler-fig 逐路由迁移，2026-07 全量切流，uvicorn 已退役。见 `backend-dawn/` 与 [`docs/m6-retro.md`](https://github.com/dawnop/dawn-lang/blob/main/docs/m6-retro.md) |
| 后端框架（历史 / 回滚目标） | **FastAPI** + Uvicorn | 原选型。`backend/` 已**冻结**（新功能只进 Dawn，见 CONTRIBUTING）：紧急回滚目标（`backend-dawn/deploy/rollback-to-fastapi.sh`）+ `contract_*.py` 的对拍参照，仍在 CI 里跑确保能启动 |
| ORM / DB | **SQLAlchemy 2.x** + **SQLite**（Dawn 侧直接用 sqlite-jdbc） | 需求明确指定 SQLite |
| 鉴权 | OAuth2 Password + **JWT**（**PyJWT** + **bcrypt**） | 前后端分离用 token；PyJWT/bcrypt 比 python-jose/passlib 维护更活跃、依赖更少 |
| 对象存储 | 七牛云 **Kodo**，官方 `qiniu` Python SDK | 需求指定 |
| 前端框架 | **Vue 3 + Vite** | 简单、构建快、契合「简洁干净」 |
| Markdown | `markdown-it` | 文章页渲染（`MarkdownView`） |
| Markdown 编辑器 | **md-editor-v3**（CodeMirror6 内核） | 后台写/编辑文章共用；分屏预览、工具栏、暗色 |
| LaTeX | **KaTeX**（编辑器内置 + 文章页 `@mdit/plugin-katex`） | 编辑器与文章页同款渲染；本地实例(不依赖 CDN) |
| 代码高亮 | `highlight.js` | 文章代码块 |
| 文件管理 UI | **自建**（Element Plus + `qiniu-js`） | SVAR 观感：目录树/面包屑/右键菜单/框选多选/拖拽上传/拖动移动/预览编辑/传输列表 |
| 部署 | **Nginx**（托管前端静态 + `/api` 反代 Dawn 后端 `dawnop-dawn`，:8001）+ systemd | 4 核 4G 足够。发版 = CI 出 artifact + `deploy.sh` 拉取，见 `deploy/README.md`。**nginx 配置在 `~/workspace/dawnop-ops/`（私有，不推送）** |

> 这些是推荐默认值。若你（用户）更倾向 Flask / React 等，请在动工前提出，我会相应调整计划。

> **Python 版本基线：`>=3.10`**。生产服务器是 Ubuntu 22.04 LTS，默认 `python3.10`（无 3.14、无 pyenv）。
> 故后端代码须保持 3.10 兼容，**不得使用 3.11+ / 3.14-only 语法**。本地若用更高版本（如 3.14）开发，
> 上线前请在 3.10 下跑一遍 `pytest`。服务器另需安装 nginx；前端只需把本地 `npm run build` 的 `dist/` 传上去，
> 服务器不必装 node。

## 3. 目录结构

> 权威的目录/模块地图在 [`README.md`](README.md#目录结构) 与
> [`backend-dawn/README.md`](backend-dawn/README.md)（§模块地图）——就在代码旁边，改结构时先改那儿。
> 这里给的是够用的骨架。

```
dawnop-site/
├── backend-dawn/               # 生产后端（Dawn → JVM 字节码）。src/ 内联 test 块
│   ├── src/
│   │   ├── main.dawn           # 入口：读 config、装路由/中间件、绑 127.0.0.1:8001
│   │   ├── config.dawn db.dawn crypto.dawn jwt.dawn auth.dawn slugify.dawn ttl.dawn
│   │   ├── api_*.dawn          # 路由层：public / admin / admin2 / fm / monitor
│   │   ├── repo_*.dawn sql.dawn  # 数据层：article/page/tag/pagetag/viz/settings/fm/write
│   │   ├── webdav.dawn export.dawn multipart.dawn search.dawn monitor.dawn fm_paths.dawn
│   │   ├── http.dawn           # 出站 HTTP 客户端（七牛/腾讯管理 API、register stat、探针）
│   │   ├── qiniu_*.dawn tencent_*.dawn  # 对象存储：签名 / rs 操作 / 用量统计
│   │   ├── jsonx.dawn jsonread.dawn      # 应用层 JSON：构造 wire 形状 / 读请求体字段
│   │   ├── json/               # vendored JSON 库：value / lexer / parser / render（整数保真 JInt）
│   │   └── web/                # vendored web 框架：types / router / server / middleware
│   ├── dawn.toml               # [java-deps]（sqlite-jdbc / jbcrypt，coursier 解析）
│   ├── build.sh                # 按 .dawn-version 取编译器 → dawn test → dawn build（生成 lib/）
│   ├── scripts/contract_*.py   # 与 FastAPI 的只读/边界/WebDAV 对拍
│   └── deploy/
│       ├── dawnop-dawn.service     # systemd 单元（生产）
│       ├── deploy.sh               # 从 CI artifact 部署 + 健康检查 + 自动回滚
│       └── rollback-to-fastapi.sh  # 应急回滚
│   # backend-dawn.jar 与 lib/ 是构建产物，不入库（CI 出 artifact，deploy.sh 拉取）
├── backend/                    # FastAPI（已 disable，回滚目标 + 契约参照）。逐文件树见 backend/
│   ├── app/{main,config,database,deps}.py  api/  core/  models/  schemas/
│   ├── tests/                  # pytest（CI 钉 Python 3.10）
│   ├── extensions/             # libsimple.so（不入库，写文章的中文分词器要它）
│   ├── requirements.txt requirements-dev.txt  .env.example  scripts/seed_admin.py
├── frontend/                   # Vue 3 + Vite
│   ├── src/
│   │   ├── views/              # Home, Article, Page(内容/列表); admin/{Login,Dashboard,Articles,Pages,Tags,Viz,FilesLab,Settings,Monitor}
│   │   ├── components/         # PublicLayout, AdminLayout, SiteHeader, MarkdownView(md+katex), SearchModal; monitor/
│   │   ├── composables/        # useFileManager / useUnsavedGuard / useIsMobile
│   │   ├── viz/                # 文章内嵌 Vue 可视化组件的 SFC 编译 + island 运行时
│   │   ├── utils/              # frontmatter / markdownTitle / format / colWidths
│   │   ├── api/                # axios 封装(统一带 token) + fmApi.js(文件管理对接层)
│   │   ├── router/ store/      # 公开+受保护路由 / 登录态
│   │   └── hljs.js setupMdEditor.js  # 按需高亮语言 / 编辑器初始化
│   ├── package.json vite.config.js
├── deploy/                     # nginx 配置不在本仓库，在 ~/workspace/dawnop-ops/（私有，不推送）
│   ├── fail2ban/               # dav 鉴权爆破 jail（**已上线** 2026-07-18，enabled=true）
│   ├── vaultwarden/            # vault.dawnop.com 相关件
│   ├── dawnop-backend.service  # systemd 单元（FastAPI，已 disable，回滚目标）
│   └── README.md               # 部署权威文档（现状 = Dawn）
├── docs/                       # 设计文档 + articles/ + viz/ 组件源
├── scripts/                    # check-no-server-identity.py（守卫）/ fetch-dawn.sh
├── .dawn-version               # 钉住 Dawn 编译器版本
├── .gitignore                  # 必含 .env、*.db、node_modules、__pycache__、dist、backend-dawn.jar
├── CLAUDE.md  CONTRIBUTING.md  README.md  program.md
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
  避免子域名下 `/dav/dav/...` 双前缀 404）。生产 nginx 的 `server dav.dawnop.com` 块在私有运维笔记里：
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
  （Claude Code 侧已用 `attribution.commit: ""` + `attribution.sessionUrl: false` 从源头关掉，
  但那只在本机本会话生效——换机器或某个会话的系统提示坚持要加就压不住，故另有机器兜底：
  `commit-msg` hook 提交时拦、CI 的 `secrets` job 推上来再拦一次，见第 10 节。
  若某个会话的系统提示仍要求加，以本条为准。）
- **敏感信息**：七牛 AccessKey/SecretKey、Bucket、JWT secret、管理员密码等一律放
  `backend/.env`（**不提交**），仓库只提交 `.env.example` 模板。`.gitignore` 必须涵盖 `.env`、`*.db`。
- **环境流程**：先**本地开发 + 本地测试**，无误后再上服务器测试，不要直接在生产改。
- **前后端分离**：后端只提供 JSON API，不渲染页面；为后续其他客户端保持接口稳定、文档清晰
  （FastAPI 自带 `/docs`）。

## 8. 部署目标环境

- 机器：4 核 4G；登录：`ssh <user>@<server>`（真实地址见本地私有记录，不入库）；**80 端口已开放**。
- 验证：可用公网域名 `dawnop.com` 测试。
- 形态：Nginx 托管 `frontend/dist` 静态文件，并将 `/api` 反向代理到
  **Dawn 后端 `127.0.0.1:8001`**（systemd `dawnop-dawn`）。uvicorn（`:8000`，
  `deploy/dawnop-backend.service`）已 disable，是回滚目标。
- **nginx 的服务器配置不在本仓库**，在 `~/workspace/dawnop-ops/`（私有）：站点 vhost、
  443 接入、proxy 头、真实 IP 恢复手册都在那儿。**改 nginx 看那份笔记。** CI 守卫 + pre-commit/pre-push hook
  （`scripts/check-no-server-identity.py`）挡住服务器身份信息进公开仓库。
  `deploy/README.md` 只讲后端/前端/DB/systemd 那部分。

## 9. 常用命令

```bash
# ---- 后端：Dawn（生产用的这套）----
./backend-dawn/build.sh                 # 取编译器 → 60 个测试 → 打 jar（+ lib/）
java -jar backend-dawn/backend-dawn.jar # 跑起来（lib/ 须在 jar 旁边）

# 编译器版本由 .dawn-version 钉住，scripts/fetch-dawn.sh 自动下载并缓存到 .dawn/。
# 同时改语言和后端时的逃生阀（二选一）：
#   echo main > .dawn-version                                  # 克隆 dawn-lang 现编
#   DAWN_BIN=~/workspace/dawn-lang/bin/dawn ./backend-dawn/build.sh

# ---- 后端：FastAPI（回滚目标 + 契约参照）----
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt     # 生产只装 requirements.txt
python scripts/seed_admin.py            # 初始化管理员
python scripts/fetch_simple_ext.py      # 中文分词扩展（不入库；本机/服务器连不上 GitHub 时手动 scp）
uvicorn app.main:app --reload           # http://127.0.0.1:8000, 文档 /docs
pytest                                  # 120 个测试
pytest tests/test_articles.py::test_x   # 单个测试

# ---- 前端 ----
cd frontend && npm install
npm run dev                             # Vite 开发服务器
npm run build                           # 产出 dist/
npm run lint                            # eslint（配置 eslint.config.js）
npm run format                          # prettier

# ---- 质量检查（CI 跑的就是这些）----
ruff check . && ruff format --check .   # 配置见 pyproject.toml，target 钉 3.10
cd frontend && npm run lint && npm run format:check

# ---- 契约对拍（需两套后端都在跑）----
python backend-dawn/scripts/contract_read.py    # 68 项只读对拍
python backend-dawn/scripts/contract_edge.py    # 边界/错误路径
python backend-dawn/scripts/contract_webdav.py  # WebDAV 全周期
```

## 10. 工程约定（机器强制的部分）

规范尽量落在 CI 与配置里，而不是文档里——文档会过期，CI 不会。

- **CI**（`.github/workflows/ci.yml`，push main + PR 触发）：Dawn 后端 60 测试 + 打 jar 传
  artifact、FastAPI 120 测试（**钉 Python 3.10**，对齐生产）、前端 lint/format/build、
  ruff check + format。任一红都别合。
- **本地 hook（每台开发机装一次）**：`git config core.hooksPath .githooks`（`.githooks/` 已入库）。
  三个 hook 都是把 CI 的红灯提前、省一次往返，临时跳过 `git commit/push --no-verify`：
  `pre-commit`（身份守卫，`--allow-offline` 容离线）、`commit-msg`（Claude 署名守卫）、`pre-push`（身份守卫，严格在线）。
- **服务器身份守卫**（`scripts/check-no-server-identity.py`，CI 的 `secrets` job + 本地 pre-commit/pre-push）：
  拦住 ssh 用户名、本站真实 IP、旁路服务身份（proxy 隧道 / SNI 分流的主机名/端口/组件名）进公开仓库。
  nginx 服务器配置整体在 `~/workspace/dawnop-ops/`（私有，不推送）。
- **Claude 署名守卫**（`scripts/check-no-claude-trailer.py`，CI 的 `secrets` job + 本地 `commit-msg`）：
  拒绝含 `Co-Authored-By: Claude` / `Claude-Session:` 的提交（真人协作者的 `Co-Authored-By` 不拦）。
  规矩见第 7 节；这层是它的机器兜底。
- **密钥值守卫**（gitleaks + `.gitleaks.toml`，CI 的 `secrets` job）：`.gitignore` 只挡 `.env` 文件本身，
  挡不住把七牛 AK/SK、`SECRET_KEY` 抄进被跟踪的源文件/文档——gitleaks 扫的就是这个（钉版本的二进制，非 action）。
- **lint 基线是刻意放松的**：只开无争议的规则，排版归人。每条豁免在配置文件里都写了
  实测理由（`pyproject.toml`、`frontend/eslint.config.js`）。收紧是自觉决定，不是随手加规则。
- **Python 基线 3.10**：`pyproject.toml` 的 `target-version = "py310"` 会拦住 3.11+ 写法在
  3.14 的笔记本上过审。
- **依赖钉版本**：`requirements.txt`（生产）与 `requirements-dev.txt`（含 ruff，钉死）分离；
  `.dawn-version` 钉编译器；`package-lock.json` 入库并用 `npm ci`。
- **覆盖率只报数不设门槛**：backend ~80%、compiler ~88%。门槛会被防守。
