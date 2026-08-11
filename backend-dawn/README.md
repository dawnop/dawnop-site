# backend-dawn

dawnop.com 博客后端的 **Dawn 重写**（dawn-lang M6，计划见 dawn-lang 仓库 `docs/m6.md`）。
与 `backend/`（FastAPI，现为冻结的回滚目标 + 契约参照）**共用同一 SQLite 库与七牛空间**；
迁移期 nginx 按路由灰度切流，**2026-07 已全量切到 Dawn**（uvicorn 退役、只回滚时拉起）。
`/api` 全部端点已迁移并与 FastAPI 逐字段对拍一致；曾留待 M6.5 的 **WebDAV**（`src/api/webdav.dawn`）
与 `POST /api/fm/upload`（multipart 代理上传，`src/util/multipart.dawn`）**也已落地**。

契约由 `scripts/golden/*.json` 钉住（`scripts/contract_run.py`，CI 每次 push 都跑）：
播种固定 fixture → 起后端 → 220 条响应逐字节比对。四套脚本里 `contract_qiniu.py` 另起一个
**指向本地假七牛**（`contract_qiniu_fake.py`）的后端，把子目录 COPY、PUT→GET 字节往返、
覆盖写换 key、register 的 stat 校验这些必须有对象存储才走得到的路径也钉住；
剩下的具名 skip 只有一件事——桶用量统计（`fm.stats`，走七牛计费/空间 API，假桶不模拟）。

## 依赖与构建

- 依赖：在 [`dawn.toml`](dawn.toml) 的 `[java-deps]` 里声明（`sqlite-jdbc`、`jbcrypt`——零传递依赖
  白名单，见 m6.md §2 G1）。`dawn run/test/build` 自动从 Maven 拉取并挂上 classpath；
  `dawn build` 另把它们复制进 jar 同级的 `lib/`，jar 的 manifest `Class-Path` 以相对路径引用它，
  **部署时保持 `lib/` 与 jar 同目录**（形态与从前一致）。`lib/` 是构建产物，不入库。
  国内网络可设 `DAWN_MAVEN_MIRROR=https://maven.aliyun.com/repository/public` 加速。
- 构建：`./build.sh [输出名]`（先 `dawn test` 后 `dawn build`；需 `dawn` 在 PATH、`JAVA_HOME` 指向 GraalVM/JDK 21）。
  **jar 与 `lib/` 都是构建产物，不入库**——jar 曾经入库，结果是它悄悄落后于 `src/`（要靠手动
  「重建 jar」提交追平），而 `lib/` 本就 ignore，从 checkout 里那个 jar 根本跑不起来。
  现在由 CI 构建并上传 artifact，部署取的就是它。
- 测试：`dawn test .`（`src/` 共 42 个 Dawn 源文件、80 个本仓单测，连 web/json/sha2 三个包
  共 148 个；`use java` import 共 61 条、分布在 12 个文件；无需 .env / 库 /
  libsimple / 网络，CI 每次 push 都跑）。用到 SQLite 的几个跑内存库（`jdbc:sqlite::memory:`），
  自带建表，不碰 fixture。
- 运行：`java -jar backend-dawn.jar`（读 `DAWNOP_ENV` 指定的 .env，默认 `backend/.env`；
  绑定 `127.0.0.1:$DAWN_PORT`，默认 8001）。

## 关键环境变量

与 FastAPI 共享的（`SECRET_KEY`、`QINIU_*`、`TENCENT_*`、`LIGHTHOUSE_INSTANCE_ID`、
`VAULT_ALIVE_URL` 等）逐字取同一 .env；Dawn 专属：

- `DAWN_PORT`（默认 8001）、`DAWN_CORS_ORIGIN`（默认 `https://dawnop.com`）。
- `DAWN_DB_PATH`（默认 `backend/dawnop.db`；生产 `/opt/dawnop/data/dawnop.db`，与 FastAPI 同文件，WAL 共享）。
- `DAWN_SIMPLE_EXT`（`libsimple` 路径，无扩展名；写 `articles` 触发 FTS `tokenize='simple'` 必须加载）。

## 模块地图

`src/` 按层分目录，依赖单向向下：`util → db → qiniu → repo → tencent → svc → api → (根)`。
根只留 `main.dawn` 与 `config.dawn`。`qiniu_*`/`tencent_*` 进目录后丢前缀
（`qiniu/sign`、`tencent/client`），`api_*`/`repo_*` 保留前缀——api 与 repo 是唯一会同名对撞的两层。

**基础设施（预备刀 1–5）**
- `db/sql.dawn` — SQLite JDBC 薄包装：`SqlV/Col/Cell` ADT、`query/exec/with_tx`、类型化取值、
  `first_*` 首行取值（`match get(rows, 0)` 的样板收在这里）。
  JDBC `Connection` 只在本模块可见，其他模块统一使用公开不透明的 `DbConn`。
  **按列名读行（`query_rows` + `row.col_int("id")`）只用在选择列表是共享常量的三处**：
  `repo_article` 的 `LIST_COLS`(12 列)、`repo_page` 的 `PAGE_COLS`(11 列)、`repo_viz` 的
  `OUT_COLS`(8 列)——这类列表被多条查询共用，加一列就得数所有下标，位置索引在这里是负担。
  其余仓（tag / fm / export / auth / search）是 1–4 列的就地 select，下标与 select 同屏可见，
  按位置读更短也不易错，**故意保留**，不是漏迁。
- `util/ferr.dawn` — `ForeignError` → 旧版 `Throwable.toString()` 文本（`fe_text`）：v0.33.0+ 的
  `catch_fault`/`cast` 返回结构化 `ForeignError`，各屏障模块经它转回 `Result[T, String]`，错误文案与升级前逐字节一致。
- `db/db.dawn` — 每请求一连接（`with_db`）：WAL + `load_extension(libsimple)`。
- `util/crypto.dawn` — sha256 / hmac-sha1 / hmac-sha256 / base64url / uuid（auth、七牛、JWT、腾讯共用）。
- `util/http.dawn` — java.net.http 出站客户端：`fetch/post/post_form` + `fetch_bytes`（二进制体，G6）；
  `RequestBody` 不透明边界封装流式文件请求体，调用方不传播 Java publisher 类型。
- `util/jsonx.dawn` / `util/jsonread.dawn` — JSON 构造（`obj/jint/jstr/jopt_*`）/ 请求体读取（`opt_int/str_or/str_list`）。
- `json` — **dawn-lang 的 `packages/json`**（`[deps.json]` url+hash 依赖，vendored 副本已删）；游标版解析器，整数字面量产 `JInt`（保真、免 round-trip 变 `x.0`）。
- `config.dawn` — .env 读取，env 优先（对齐 pydantic-settings 精度）。
- `web` — **dawn-lang 的 `packages/web`**（`[deps.web]` url+hash 依赖，vendored 副本已删）：`server`（HttpServer + G6 二进制响应体 + 流式）/`router`（tags/任意动词）/`types`/`middleware`（logging/cors/body-limit）。

**鉴权（刀 7）**
- `svc/auth.dawn` — `Auth`/`Qiniu` 配置类型、`current_user`（Bearer + `?token=`）、login/me；jBCrypt 校验（`$2b$→$2a$` 归一）。
- `util/jwt.dawn` — HS256 签发/校验，与 PyJWT 双向互认。

**公开只读（刀 6/8）**
- `api/api_public.dawn` — health/search/viz/`{slug}`/articles/pages/tags 路由。
- `repo/repo_article.dawn` / `repo/repo_page.dawn` / `repo/repo_tag.dawn` — 只读查询 + `word_count`/分页/标签。

**后台写（刀 9/10）**
- `api/api_articles.dawn` — 文章 CRUD + Markdown 导出。
- `api/api_settings.dawn` / `api/api_tags.dawn` / `api/api_pages.dawn` / `api/api_viz.dawn` — 一文件一后台页面。
  （曾是一个 `api_admin2.dawn`：它不是从 `api_admin` 拆出来的，是刀 9 的新功能进了新文件、
  名字随手叫了 `2`，于是文件名记的是写作顺序而非内容。2026-07-19 按内部已有的分区注释拆开。）
- `repo/repo_write.dawn` / `repo/repo_pagetag.dawn` / `repo/repo_viz.dawn` / `repo/repo_settings.dawn` — 写模型（唯一 slug、标签解析、校验、reorder）。
- `util/slugify.dawn` / `svc/export.dawn` — slug 生成（对齐 `core/slug.py`）/ Markdown frontmatter 导出。
- `svc/search.dawn` — FTS5 排序委托同一 SQL bm25，仅高亮在 Dawn 侧重写。

**文件管理（刀 11）**
- `api/api_fm.dawn` — 17 端点（除 `upload`）：列目录 / 预览 / 下载（302）/ 内容代理（二进制）/ stats / search / CRUD / save / register / upload-token。
  只剩路由与线格式，树操作在 `svc/files.dawn`。
- `svc/files.dawn` — 文件树操作层：①`api_fm` 与 `webdav` 共用的对象存储原语（signed_url / rebase / superseded_key / gc_superseded / copy_object / delete_object_of）——
  只回 Result 不映射状态码，因为两个调用方的错误映射与连接持有方式本就不同（fm 一棵树一条连接，WebDAV 一步一条）；②`api_fm` 自己的树遍历（rename/move/copy/delete/save/upload）。
- `qiniu/sign.dawn` — 三类七牛签名：上传凭证、私有下载 URL、QBox 管理、QiniuMacAuth（统计/CDN/账单，含 body）。
- `qiniu/rs.dawn` — 管理 REST：stat/delete/copy/upload_text/upload_bytes/upload_file。
- `util/paths.dawn` / `repo/repo_fm.dawn` — 路径原语 / 虚拟树（path↔key，DirEntry 序列化）。

**监控（刀 12）**
- `api/api_monitor.dawn` — `/api/monitor`，120s TTL + `?refresh`，配额从 settings 表实时注入。
- `svc/monitor.dawn` — 四块容错聚合：server（/proc，回落 JMX）、lighthouse（TC3）、qiniu（kodo+CDN+respack）、vault 探活。
- `tencent/client.dawn` / `tencent/sign.dawn` — 腾讯云 v3 请求装配 / TC3-HMAC-SHA256 签名（vs SDK 逐字节）。
- `qiniu/stats.dawn` — kodo v6 序列 / fusion CDN tune / billing respack。
- `util/ttl.dawn` — `AtomicReference` TTL cache cell（monitor 120s、respack 300s 共享、fm space 600s）。

**入口**
- `main.dawn` — 读配置、建 `Auth`/`MonCfg`、拼装全部路由 + 中间件、绑定端口。

## 部署

```bash
ssh <user>@<server> 'sudo bash -s' < backend-dawn/deploy/deploy.sh          # main 最新绿
ssh <user>@<server> 'sudo bash -s' < backend-dawn/deploy/deploy.sh <sha>    # 指定提交
```

取该提交 CI 产物（`backend-dawn-<sha>`），故**上线的就是 CI 测过的那份**。校验 jar 与
manifest `Class-Path` 要的 `lib/` 齐全后才装，起不来自动回滚到 `.prev/`。幂等（同 sha 不重装）。
服务器需 `/opt/dawnop-dawn/.github-token`（fine-grained PAT，Actions: read-only）——
artifact 下载即使公开仓也要鉴权。

见 `deploy/`：
- `deploy.sh` — 上面那个。
- `dawnop-dawn.service` — systemd 单元（`/opt/dawnop-dawn`，`-Xmx256m`，`User=dawnop`）。
- `nginx-cutover.md` — M6 分阶段切流记录 + 全量切换 snippet（已执行）。
- `rollback-to-fastapi.sh` — 应急回退到 FastAPI（uvicorn 已 disable，脚本会拉起）。
