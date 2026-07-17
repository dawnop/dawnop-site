# backend-dawn

dawnop.com 博客后端的 **Dawn 重写**（dawn-lang M6，计划见 dawn-lang 仓库 `docs/m6.md`）。
与 `backend/`（FastAPI，现为冻结的回滚目标 + 契约参照）**共用同一 SQLite 库与七牛空间**；
迁移期 nginx 按路由灰度切流，**2026-07 已全量切到 Dawn**（uvicorn 退役、只回滚时拉起）。
`/api` 全部端点已迁移并与 FastAPI 逐字段对拍一致；曾留待 M6.5 的 **WebDAV**（`src/webdav.dawn`）
与 `POST /api/fm/upload`（multipart 代理上传，`src/multipart.dawn`）**也已落地**，`contract_webdav.py` 全周期对拍通过。

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
- 测试：`dawn test .`（60 个单测，无需 .env / 库 / libsimple / 网络，CI 每次 push 都跑）。
- 运行：`java -jar backend-dawn.jar`（读 `DAWNOP_ENV` 指定的 .env，默认 `backend/.env`；
  绑定 `127.0.0.1:$DAWN_PORT`，默认 8001）。

## 关键环境变量

与 FastAPI 共享的（`SECRET_KEY`、`QINIU_*`、`TENCENT_*`、`LIGHTHOUSE_INSTANCE_ID`、
`VAULT_ALIVE_URL` 等）逐字取同一 .env；Dawn 专属：

- `DAWN_PORT`（默认 8001）、`DAWN_CORS_ORIGIN`（默认 `https://dawnop.com`）。
- `DAWN_DB_PATH`（默认 `backend/dawnop.db`；生产 `/opt/dawnop/data/dawnop.db`，与 FastAPI 同文件，WAL 共享）。
- `DAWN_SIMPLE_EXT`（`libsimple` 路径，无扩展名；写 `articles` 触发 FTS `tokenize='simple'` 必须加载）。

## 模块地图

**基础设施（预备刀 1–5）**
- `sql.dawn` — SQLite JDBC 薄包装：`SqlV/Col/Cell` ADT、`query/exec/with_tx`、类型化取值。
- `db.dawn` — 每请求一连接（`with_db`）：WAL + `load_extension(libsimple)`。
- `crypto.dawn` — sha256 / hmac-sha1 / hmac-sha256 / base64url / uuid（auth、七牛、JWT、腾讯共用）。
- `http.dawn` — java.net.http 出站客户端：`fetch/post/post_form` + `fetch_bytes`（二进制体，G6）。
- `jsonx.dawn` / `jsonread.dawn` — JSON 构造（`obj/jint/jstr/jopt_*`）/ 请求体读取（`opt_int/str_or/str_list`）。
- `json/`（vendored）— `value`/`lexer`/`parser`/`render`；解析器对整数字面量产 `JInt`（保真、免 round-trip 变 `x.0`）。
- `config.dawn` — .env 读取，env 优先（对齐 pydantic-settings 精度）。
- `web/`（vendored）— `server`（HttpServer + G6 二进制响应体）/`router`/`types`/`middleware`（logging/cors/body-limit）。

**鉴权（刀 7）**
- `auth.dawn` — `Auth`/`Qiniu` 配置类型、`current_user`（Bearer + `?token=`）、login/me；jBCrypt 校验（`$2b$→$2a$` 归一）。
- `jwt.dawn` — HS256 签发/校验，与 PyJWT 双向互认。

**公开只读（刀 6/8）**
- `api_public.dawn` — health/search/viz/`{slug}`/articles/pages/tags 路由。
- `repo_article.dawn` / `repo_page.dawn` / `repo_tag.dawn` — 只读查询 + `word_count`/分页/标签。

**后台写（刀 9/10）**
- `api_admin.dawn` / `api_admin2.dawn` — 文章 CRUD + 导出；设置 / 标签管理 / 页面 / viz。
- `repo_write.dawn` / `repo_pagetag.dawn` / `repo_viz.dawn` / `repo_settings.dawn` — 写模型（唯一 slug、标签解析、校验、reorder）。
- `slugify.dawn` / `export.dawn` — slug 生成（对齐 `core/slug.py`）/ Markdown frontmatter 导出。
- `search.dawn` — FTS5 排序委托同一 SQL bm25，仅高亮在 Dawn 侧重写。

**文件管理（刀 11）**
- `api_fm.dawn` — 17 端点（除 `upload`）：列目录 / 预览 / 下载（302）/ 内容代理（二进制）/ stats / search / CRUD / save / register / upload-token。
- `qiniu_sign.dawn` — 三类七牛签名：上传凭证、私有下载 URL、QBox 管理、QiniuMacAuth（统计/CDN/账单，含 body）。
- `qiniu_rs.dawn` — 管理 REST：stat/delete/copy/upload_text/space_used。
- `fm_paths.dawn` / `repo_fm.dawn` — 路径原语 / 虚拟树（path↔key，DirEntry 序列化）。

**监控（刀 12）**
- `api_monitor.dawn` — `/api/monitor`，120s TTL + `?refresh`，配额从 settings 表实时注入。
- `monitor.dawn` — 四块容错聚合：server（/proc，回落 JMX）、lighthouse（TC3）、qiniu（kodo+CDN+respack）、vault 探活。
- `tencent_client.dawn` / `tencent_sign.dawn` — 腾讯云 v3 请求装配 / TC3-HMAC-SHA256 签名（vs SDK 逐字节）。
- `qiniu_stats.dawn` — kodo v6 序列 / fusion CDN tune / billing respack。
- `ttl.dawn` — `AtomicReference` TTL cache cell（monitor 120s、respack 300s 共享、fm space 600s）。

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
