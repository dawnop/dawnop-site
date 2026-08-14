# backend-dawn

dawnop.com 博客后端的 **Dawn 重写**（dawn-lang M6，计划见 dawn-lang 仓库 `docs/m6.md`）。
与 `backend/`（FastAPI，现为冻结的回滚目标 + 契约参照）**共用同一 SQLite 库与七牛空间**；
迁移期 nginx 按路由灰度切流，**2026-07 已全量切到 Dawn**（uvicorn 退役、只回滚时拉起）。
`/api` 全部端点已迁移并与 FastAPI 逐字段对拍一致；曾留待 M6.5 的 **WebDAV**（`src/api/webdav.dawn`）
与 `POST /api/fm/upload`（multipart 代理上传，`src/util/multipart.dawn`）**也已落地**。

契约由 `scripts/golden/*.json` 钉住（`scripts/contract_run.py`，CI 每次 push 都跑）：
播种固定 fixture → 起后端 → 300 条响应比对（read 48 / edge 85 / webdav 103 / qiniu 64）。

**比的是什么，别当成「逐字节」**——三种粒度，各自的理由写在 `contract_golden.py` 与
`contract_webdav.py` 的注释里：

- **JSON 响应体是结构比较**：录进 golden 前先 `json.loads`，比的是解析后的值，因此键序与
  空白不参与。这是有意的：wire 上的键序由 `jsonx` 的构造顺序决定，不是契约。
- **WebDAV 的 XML 体是 scrub 之后的文本比较**：本次运行创建的资源，其
  `getlastmodified`/`creationdate` 换成 `WALL-CLOCK`、`opaquelocktoken:<uuid>` 归一
  （fixture 行的时间戳保持钉死，这个区分本身就是要点）；七牛签名 URL 的
  `?e=…&token=…`、每次新生成的 32 位 hex key、假桶的 base URL 同样归一。
- **只有专门的案例持有真实响应字节的合同**：`propfind.depth.invalid.fail-closed`、
  `copy.dest.host.singleton.fail-closed`、`fm.persisted-mime.fail-safe` 三条走裸 socket
  （`raw_http`），录状态行、头名字顺序、不合语法的头行与 body 字节数——重复的请求头和被注入
  的响应头只有在这一层看得见，urllib 表达不出来。

四套脚本里 `contract_qiniu.py` 另起一个**指向本地假七牛**（`contract_qiniu_fake.py`）的后端，
把子目录 COPY、PUT→GET 字节往返、覆盖写换 key、register 的 stat 校验这些必须有对象存储才走得到
的路径也钉住；假桶会重算每个 HMAC 签名，并校验上传凭证 putPolicy 的 `deadline` 仍在有效期内且
不超过一天，所以「签出来的凭证真能用」是花掉它换来的结论，不是看它长得对。
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
- 测试：`dawn test .`（`src/` 共 42 个 Dawn 源文件、162 个本仓单测，连 web/json/sha2 三个包
  共 230 个；`use java` import 共 61 条、分布在 12 个文件，另有 11 条 FFI 边界断言；无需 .env / 库 /
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

共享的那批里有一个带边界：`QINIU_TOKEN_EXPIRES`（默认 3600）只收 **1..86400**（闭区间，单位秒）。
它是本后端签的每一个七牛 deadline——直传凭证、私有下载 URL、代理上传——的时效。写了但不合法
（非数字、0、负数、超过一天）**在启动时 fail closed**，不回落默认值：默认值是「没写」的意思，
拿它去兜一个写错的值，等于把打错的字变成一台跑着、且没人知道凭证活多久的服务器。判词在
`config.int_in_range`，越界与非数字各有一条 Dawn 单测和一个变异体；假七牛那侧另有
`fm.upload-token.deadline-window` 真去花掉一次凭证。

## 模块地图

`src/` 按层分目录，依赖单向向下：`util → db → qiniu → repo → tencent → svc → api → (根)`。
根只留 `main.dawn` 与 `config.dawn`。`qiniu_*`/`tencent_*` 进目录后丢前缀
（`qiniu/sign`、`tencent/client`），`api_*`/`repo_*` 保留前缀——api 与 repo 是唯一会同名对撞的两层。

**基础设施（预备刀 1–5）**
- `db/sql.dawn` — SQLite JDBC 薄包装：`SqlV/Col/Cell` ADT、`query/exec/with_tx`、类型化取值、
  `first_*` 首行取值（`match get(rows, 0)` 的样板收在这里）。
  JDBC `Connection` 只在本模块可见，其他模块统一使用公开不透明的 `DbConn`。
  **「这条连接上是不是已经有事务」只有 SQLite 答得了**（`nested_tx_refusal`，两个事务入口与
  `require_tx` 共读这一处判据）：sqlite-jdbc 自留一份 JDBC 层 autoCommit 标志，而本后端的事务
  全部由裸 `begin immediate` 开出，压根不碰那个标志——实测在 `with_immediate_tx` 体内
  `getAutoCommit()` 仍读到 true。两个入口原先用它当守卫，既发不出来，也不该发：真发出来时它
  回滚的是**外层调用方**的未提交写入。现在两个入口都**拒绝**而不是回滚，把它找到的事务原样留下
  （`with_immediate_tx` 直接读 `begin immediate` 自己的拒绝，不额外探测；`with_tx` 用 `in_tx`
  探一次，它没有生产调用方）。负控在 `scripts/db-connection-boundary-mutants/`。
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
  **每个出站请求都带单请求超时**。`request_for` 是全模块唯一装配请求的地方，也是唯一一处 `.timeout(`，
  新加的 body handler 想漏掉超时都没有位置。分两档：管理类调用 `MANAGEMENT_TIMEOUT_S` = 10s
  （七牛 rs stat/delete/copy、用量与 CDN、腾讯云、vault 探活），搬字节的调用
  `TRANSFER_TIMEOUT_S` = 3600s（代理上传、WebDAV PUT、下载代理）。
  之所以必须分档：实测（JDK 26）这个超时是**整次交换的硬期限**，不是空闲计时器——稳定推进的响应体
  照样在期限处被切断，POST 请求体上传到一半也一样，而且期限还覆盖 connect 阶段。于是管理档要短
  （`svc/files.dawn` 的 gc 刻意把 `delete_obj` 关在 `BEGIN IMMEDIATE` 里防 TOCTOU，这个值就是
  SQLite 全局写锁被占的上界），传输档必须远高于任何合法传输，因为它是时长上限而不是延迟上限。
  超时的错误文本写明超的是哪一档（`outbound HTTP timed out after Ns: ...`），**其余传输错误逐字节不变**。
  负控在 `scripts/http-timeout-mutants/`：假服务器接受连接后先停住再迟迟作答（有限停顿，所以变异体
  是转红而不是把门禁挂死），`inflate-deadline` 保留 `.timeout(` 只把值改荒谬——只有真计时的断言能判红它。
  `RequestBody` 不透明边界封装流式文件请求体，调用方不传播 Java publisher 类型；
  `ResponseStream` 不透明边界封装实时响应流，业务模块只经 owner adapter 进入 web3 streaming。
  FFI 门禁只约束显式 `InputStream` 源码名，不阻止 Java 返回类型由推断得到；响应侧另以
  `Stream`/`ResponseBody` 与 `streaming` 两个独立 seam 门禁阻止业务模块绕过 owner。扫描时忽略
  普通、三引号与 raw string 的文本，但 `$name` 和 `${expr}` 插值表达式仍按 Dawn 代码检查。
- `util/jsonx.dawn` / `util/jsonread.dawn` — JSON 构造（`obj/jint/jstr/jopt_*`）/ 请求体读取（`opt_int/str_or/str_list`）。
  `body_obj` 先按正式 parser 解析（错误文案不变），再用同一个 `json/lexer` 走第二遍，
  拒绝**同一 object 内的重复成员**——解析进 Map 等于默默选了「后写的赢」，而
  `{"name":"a","name":"b"}` 建出来的是哪个目录，客户端并没有说。比较的是**解码后**的名字
  （`"a"` 与 `"a"` 同名），逐 object 独立记名（`{"a":{"k":1},"b":{"k":2}}` 合法），
  嵌套 object 与数组里的 object 一样查。记名用 Map 不用 List：2MB 体上限对应十几万成员，
  线性查表是二次的（实测 5 万成员 11s → 0.2s）。唯一归属 `fm.json.duplicate-members`。
- `json` — **dawn-lang 的 `packages/json`**（`[deps.json]` url+hash 依赖，vendored 副本已删）；游标版解析器，整数字面量产 `JInt`（保真、免 round-trip 变 `x.0`）。
- `config.dawn` — .env 读取，env 优先（对齐 pydantic-settings 精度）；`int_in_range` /
  `require_int_in_range` 是带上下界的整数项，写错就在启动时 panic（见上「关键环境变量」）。
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
- **写入的每一段名称必须等于自己的 `str.trim`，且不能是 `.` 或 `..`**（`util/paths.dawn` 的
  `require_admissible_names`）。
  12 个写入入口各自守住自己要写的整条相对路径：FM 的 create-folder / rename / move / copy /
  create-file / save / upload-token / register / upload，WebDAV 的 PUT / MKCOL / MOVE+COPY
  （后两者共用一处 `Destination` 守卫）。违反回 400，三种失败各有自己的消息。
  create-folder / create-file / rename 从前在这里 `str.trim` 后照建，前端也 trim；两层现在都不再
  改写用户输入。判词与寻址是分开的：`fm_split` 保持无损，所以已存在的「a.txt 」仍可列目录、下载、
  改名改干净、删除，只是不能再被创建、移动或复制到别处。拒的空白集合就是 `str.trim` 的
  （`Character.isWhitespace`：含 U+3000，不含 U+00A0 / U+2007 / U+202F / U+200B / U+FEFF），
  不是「不可见字符」。
  拒 `.` / `..` 的理由不是路径穿越（这里没有任何东西解析它们，建出来的只是一行 path 写作
  `docs/.` 的普通行），而是**那行只有一个界面够得着**：WebDAV 在把请求路径转成 rel 时就拒绝点号段，
  于是网页文件管理器能列能开的东西，挂载客户端永远指不到，删不掉也移不走。同一棵树的两个界面，
  一个拼不出的名字就不该存在。判词只管写入，遗留的 `.` 行仍可列目录、改名改干净、删除。
  这个字面量在 `util/paths.dawn` 与 `api/webdav.dawn` 各有一份，是刻意的：前者是写入准入、
  后者是寻址本身（读也拒），且两层的 mutant 必须能各自唯一归属，共用一份会让两边都归属不了。
- **create 的 `name` 可以是多段，rename 的不行**，这是产品决定不是疏漏：rename 就地替换最后一段，
  多段名对它没有意义；create-folder 把 name 接在当前目录下，upload-token 读的是同一个字段，而
  文件夹上传送的正是浏览器的 `webkitRelativePath`（前端 `api/fmApi.js`），指望后端把树重建出来。
  两条路唯一共用的规则就是上面那条写入判词。想把两者「统一成一条判词」的改动会被
  `fm.create-folder.subpath-name` 合同挡下。
- **`files.content_type` 出站一律过 `safe_persisted_mime`**（整体替换成 `application/octet-stream`，
  不做清洗）：PROPFIND 的 `getcontenttype`、HEAD/GET 的 Content-Type、`/api/fm/content` 代理、
  save 复用旧类型、七牛 multipart 头、代理上传申报的类型，加上 DirEntry 的 `mime_type` 与 COPY
  写进新行的类型。`thumb_fop` 不在此列，它只拿这个值跟固定的光栅类型白名单比对，值本身不外流。
- `api/webdav.dawn`：`Destination` 只把 path-absolute 或 authority 与请求 `Host` 归一后相同的
  HTTP(S) absolute URI 映射到本地树。`Request` 没有可信的外部 scheme 字段，因此 `Host` 未带端口时
  同站的 HTTP 与 HTTPS 形式都接受，各自按 80/443 归一；其他 scheme、远端 authority、query 与
  fragment 都返回 400，不会只取 path 后写入本地。本机 authority 的唯一来源是 `Host`，故要求它
  **恰好出现一次**：缺失和重复各回一条自己的 400。重复此前是「取第一条」，于是
  `Host: dav.example` 后跟 `Host: evil.example` 被接受、两条对调则被拒，答案由头的顺序决定。
  前缀比较只展开 percent-encoded unreserved，路径段按严格 UTF-8 解码。
  27 个 fail-closed mutant 中 26 个由完整 Dawn test 唯一归属，1 个由 qiniu golden 唯一归属
  （`scripts/webdav-destination-mutants/`，矩阵 v5）；
  overwrite purge 的祖先顺序由下述更强的完整文件树合同统一持有，不在这里重复造 owner。
- `repo/repo_fm.dawn`：所有新写入与重挂接都守完整祖先类型。FM 写入与整个多源 move 请求各自在一个
  `BEGIN IMMEDIATE` 事务内补缺失目录并完成最终写入；多源 move 按请求顺序逐项复验和重挂，后项失败会
  回滚同一请求内的全部前项；rename 允许缺失祖先但不补目录；WebDAV
  要求完整父链已存在且都是目录。普通文件写入会在七牛或账本副作用前拒绝目标根下已有后代，并在最终
  即时事务内复验，避免并发遗留后代与新文件根并存。move、rename 与 COPY 都在最终写事务内复验目标完整子树，不会在
  缺失目标根下留下或覆盖未映射后代。子树查询使用字面前缀且按路径父项优先，
  干净子树仍可从遗留脏外部祖先下移出。FM 与 WebDAV COPY 会在对象操作前拒绝携带 key 的目录、
  缺少有效非空 key 的文件、映射目标冲突和目标根下的未映射遗留项，随后把全部 metadata 在一个即时
  事务内复验源快照、对象形状、完整目标子树与父链并一次提交。FM COPY 将冲突、七牛失败和普通
  SQLite/内部失败分别映射为 409、502 和 500。85 个 fail-closed mutant 按依赖层精确归属到 31 条
  Dawn 单测、51 条逐项重置数据库、假七牛与调用日志的 HTTP 合同，以及 3 条 qiniu golden
  （`scripts/fm-ancestor-contract/`，矩阵 v20）。其中 `create-folder-rejects-subpath-name` 方向相反：
  它给 create-folder 加上 rename 的单段规则，钉住的是「多段名是有意接受的」。Dawn
  角色只核对自己的单测红集；
  HTTP 角色必须保持全部 Dawn 单测绿色，再唯一打红自己的合同，避免把低层事务退化对上层的真实影响
  误判为同层 collateral。
- SQLite 与七牛无法组成分布式事务。上传、保存和 COPY 在对象操作前做只读预检，SQLite 最终写在
  自己的即时事务内复验；代理上传和其他覆盖写总是使用新 key。代理上传与 /register 的收据行都在写它的
  那个即时事务内读回（`repo_fm.receipt_row` 的 `require_tx` 挡住读跑到事务外），响应描述的因此是本次
  请求提交的行，而不是提交之后碰巧在那个 path 上的行。DELETE、PUT 覆盖、COPY 覆盖与 MOVE
  覆盖都先原子提交 metadata 删除或切换，再按事务返回的真实旧 key 做引用感知回收，远端失败不会留下
  指向已删对象的旧 metadata。当前回收会在一个 `BEGIN IMMEDIATE` 内复验 `files` 与
  `pending_uploads`，并在持有写锁时调用七牛 DELETE；慢或无响应的对象存储会阻塞其他 SQLite writer。
  DELETE 失败也没有持久化重试记录。COPY 与上传若远端已写成但响应丢失，调用方拿不到新 key，同样会
  留下无法从本地账本恢复的孤儿对象。这些是当前明确记录的分布式生命周期缺口，不宣称统一原子性。
- `svc/files.dawn`：文件树操作层。①`api_fm` 与 `webdav` 共用的对象存储原语（`signed_url`、
  `copy_object`、引用感知 GC），只回 `Result` 或 best-effort 结果，不在本层映射 HTTP 状态；②`api_fm`
  自己的树遍历（rename/move/copy/delete/save/upload）。
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
