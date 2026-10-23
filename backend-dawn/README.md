# backend-dawn

dawnop.com 博客后端的 **Dawn 重写**（dawn-lang M6，计划见 dawn-lang 仓库 `docs/m6.md`）。
与 `backend/`（FastAPI，现为冻结的回滚目标 + 契约参照）**共用同一 SQLite 库与七牛空间**；
迁移期 nginx 按路由灰度切流，**2026-07 已全量切到 Dawn**（uvicorn 退役、只回滚时拉起）。
`/api` 全部端点已迁移。M6 期间逐路由与 FastAPI 对拍过，但**今天的契约是 `scripts/golden/*.json`
而不是 FastAPI**：钉住的是 Dawn 自己的答案，其中若干处与 FastAPI 刻意不同，见
[「与 FastAPI 的已知分歧」](#与-fastapi-的已知分歧)。曾留待 M6.5 的 **WebDAV**（`core/src/api/webdav.dawn`）
与 `POST /api/fm/upload`（multipart 代理上传，`core/src/util/multipart.dawn`）**也已落地**。

契约由 `scripts/golden/*.json` 钉住（`scripts/contract_run.py`，CI 每次 push 都跑）：
播种固定 fixture → 起后端 → 每臂 355 条响应比对（read 58 / edge 102 / webdav 104 / qiniu 91）。
`backend-dawn/scripts/dual-arm/run.sh` 构建 native，先跑四个负控，再跑独立 JVM/native
两臂与另计的真实 simple FTS 路由。`contract_run.py` 默认仍为 JVM；`--arm both
--native-binary /path/to/ELF` 使用同四套脚本、归一化与七个 skip。native 测试 listener
只 accept 并把 socket 交给每请求一个实际 ELF，strace 追踪全部 exec，PATH 只提供 curl；
包括迁移的任意 Java/JAR 执行尝试都会拒绝。

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
不超过一天，所以「签出来的凭证真能用」是花掉它换来的结论，不是看它长得对。上传链接（drop）
签的凭证另带 `insertOnly` 与 `fsizeLimit`，假桶照真桶的样子执行这两条（超限 413、已存在 614），
于是「凭证不能覆盖、不能超额」也是花出来的，见 `drop.direct.insert-only` 与 `drop.quota`。
剩下的具名 skip 是两类共 7 条（`golden/*.json` 的 `skipped` 里逐条列着理由）：桶用量统计
3 条（read/edge/qiniu 各一，走七牛计费/空间 API，假桶不模拟），以及 `webdav` 那套里搬字节的
4 条（`get.file`/`put.new`/`copy.file`/`delete.file`）——它跑在没有凭据的后端上，同样的路径
由 `contract_qiniu.py` 在假桶上钉住，所以是移交而不是漏掉。

**变异体只写带哨兵的一次性目录。** 九个 harness 的 `mutate.py` 都是就地改写源码、没有撤销，
所以它们在动任何文件之前先过 `scripts/mutant_workdir.py`：目标既不能是 mutator 自己所在的仓库
（这条没有逃生阀），又必须带一个 `.mutant-workdir` 文件。`run.sh` 在 `cp` 出临时副本之后立刻
建这个哨兵；手工调单个变异体时，自己在临时目录里 `touch` 一次。**哨兵本身就是逃生阀**，所以没有
`--force` 也没有环境变量：把一个目录标成可牺牲，应当是留在那个目录里、看得见的一次性动作，
而不是能 export 进 shell 配置然后忘掉的习惯。拒绝时退出码是 3（区别于 argparse 的 2 与锚点找不到
的 1）。这道守卫自己也有负控，`mutant_workdir.py --self-test`，九个 run.sh 各调一次。
起因是 2026-08-15 真出过两次：一次把活工作树里的 `repo_fm`/`repo_tag` 改了（提交前的
`git status` 才发现），一次指错成仓库根、后续步骤跑在未变异的源码上报了 PASS。

## 依赖与构建

- 编译器钉在 v0.99.0；web 8.0.0（纯 Dawn，包名 web8）、web_jvm 2.0.0（JVM 服务器，包名 web_jvm2）、
  json 与 sha2 都来自同版 tag 归档。入口使用 `web_jvm/server`，表单解析使用
  `web/wire`；JVM HTTP owner 在响应臂内用 `input_source` 把私有
  InputStream 转成 core ResponseStream 所包装的 BodySource。源的 read(pos, n) 接收累计字节偏移；
  input_source 保持原调用方式，由 InputStream 自己推进游标。
- 依赖：在 [`app/jvm/dawn.toml`](app/jvm/dawn.toml) 的 `[java-deps]` 里声明（现只剩 `sqlite-jdbc:3.53.4.0`；`jbcrypt` 已由纯 Dawn 的 `util/bcrypt` 取代——零传递依赖
  白名单，见 m6.md §2 G1）。`dawn run/test/build` 自动从 Maven 拉取并挂上 classpath；
  `dawn build` 另把它们复制进 jar 同级的 `lib/`，jar 的 manifest `Class-Path` 以相对路径引用它，
  **部署时保持 `lib/` 与 jar 同目录**（形态与从前一致）。`lib/` 是构建产物，不入库。
  国内网络可设 `DAWN_MAVEN_MIRROR=https://maven.aliyun.com/repository/public` 加速。
- 构建：`./build.sh [输出名]`（先 `dawn test` 后 `dawn build`；需 `dawn` 在 PATH、`JAVA_HOME` 指向 GraalVM/JDK 21）。
  构建后 `scripts/check_jdbc_artifact.py` 对照字节锁核对完整 lib、摘要与 manifest，
  遗留旧 jar 即失败；从旧驱动升级的本地工作区应先清理旧 lib 产物再构建。
  build 还执行 `scripts/jdbc-init/run.py` 的旧引擎、吞 WAL、吞扩展及真实旧 jar 负控。
  **jar 与 `lib/` 都是构建产物，不入库**——jar 曾经入库，结果是它悄悄落后于 `src/`（要靠手动
  「重建 jar」提交追平），而 `lib/` 本就 ignore，从 checkout 里那个 jar 根本跑不起来。
  现在由 CI 构建并上传 artifact，部署取的就是它。
- 测试：`python3 scripts/test_project.py`，由 build.sh 自动调用。test/{core,jvm} 的
  白盒测试与夹具在一次性 JVM 同模块镜像内接回同名模块，调用私有函数无需扩大生产 API。
  镜像仍使用 Dawn 路径依赖，生产构建直接消费原始 app/jvm，测试目录不进入部署 jar。
- native SQLite：仓库根执行 `backend-dawn/scripts/native-sqlite/run.sh`，使用钉住的
  dawnc 检查 adapter、构建测试 consumer、运行 native 单测、JDBC/native SQL 对拍与
  必须取得的 simple v0.7.1 FTS 夹具、真实源码负控、双进程 BUSY 和完整插桩引擎的
  ASan/UBSan/LSan。需要 clang；下载物逐次验摘要，缺失或损坏直接失败。`--benchmark 300`
  加测新进程每连接初始化成本；`--work <空目录>` 保留日志与样本。
  CI 的 S4/S5/S6/S8/S9 位于独立 `native-acceptance` job；`scripts/sqlite_ci_cache.py`
  仅缓存固定摘要下载与完整插桩 SQLite 引擎，命中仍验摘要并重跑全部负控与 sanitizer。
  缓存身份含源码摘要、编译器、参数、平台和实现摘要；`mutants-complete` 显式核对该 job 成功。
  保留原有全部 JVM 测试（S2 后含依赖共 452 项）及 11 条 FFI 断言；无需 .env、外部库或真实服务。
- curl 顺序传输：`backend-dawn/scripts/curl-transport/run.sh --work <空目录>`。
  `test/transport` 是 JVM 与 native 共用的真实出站 consumer；Python 外部进程只监听回环。
  默认执行 256/512 MiB 完整摘要、进程树 RSS/PSS、暂存磁盘与 TTFB，及五个源码负控。
  管理回复限制 4 MiB；私有请求目录、无默认 curl 配置、无代理/重定向，TLS 校验开启。
  ENOSPC 使用私有 mount namespace 的 128 KiB tmpfs，先独立证实 errno 28；CI 使用
  runner 的 sudo/unshare，不改变宿主挂载。还测实际 socket 断连、慢源、早拒绝与文件配额。
  原 JVM 生产入口不改；大文件下载暂存的 TTFB 未获 S13 放行，详见根目录
  `docs/curl-transport-design.md`。上传按追加裁决改用 upload-file，显式 method/request-target，固定长度并抑制 Expect；
  原内存阈值不变，退回 data-binary 的真实256/512 MiB负控必须红。

- native 入口：仓库根运行 `backend-dawn/scripts/native-entry/run.sh`，实际 check/build/test app/native，
  检查完整依赖图零 Java，并先证明启动 println、吞 guard 拒绝、立即关闭 socket 和请求期重跑迁移四个负控会红。
  包含手写 HTTP 字节、TCP 早拒绝/晚到 body/断连、256/512 MiB 全摘要与全进程树 RSS、单交换冷启动。
  CLI 为无参数 serve（stdin/stdout）、`--migrate`、`--cleanup-pastes` 与 `--backup <dest>`；日志均走 stderr。
  手工构建：`DAWNC=$(scripts/fetch-dawn-native.sh)` 后 `$DAWNC build backend-dawn/app/native -o /tmp/dawnop-native`。
  先用已有业务 schema 的隔离库和同一 DAWNOP_ENV 执行 --migrate，再喂请求到 stdin；此处无部署授权。
  fd teardown 在 flush 后半关闭写端，最多排空 8 MiB / 300 ms，随后退出，不会循环服务第二个交换。
  S10 在已有 backend CI job 执行，原 native-acceptance 仍跑 S2/S4/S5/S6/S8/S9，job 数与 timeout 不变。
  S8 的完整暂存 TTFB 限制继续适用；S11 的 355 双臂已接入 backend CI。
  S12 私有引擎 artifact、systemd 单元、0% 安装/撤回与离线验收命令见
  [`deploy/native-package.md`](deploy/native-package.md)；CI native-package 固定 Ubuntu 22.04。
  artifact 的 `--engine-info` 报实际私有 dlopen 路径、SQL 版本/source id，不打开业务库。
  wire/headers 只重排第一次完整 head write，保持 JVM 的 Date、207 空 reason 与文件响应头顺序；
  body 字节不进入该接缝。curl owner 接受七牛实际使用的三位状态 612，仍校验最终状态/头一致。
- core 门禁：`python3 scripts/check_core.py --negative-controls`。对整个 core 根运行
  发布版 native dawnc check，再扫描所有可达源码包的完整源码及 manifest，拒绝 Java、
  java-deps、web_jvm 与回指 app/test 的本地依赖。CI 先证明 Java 导入与 app 导入负控会红。
- 运行：`java -jar backend-dawn.jar`（读 `DAWNOP_ENV` 指定的 .env，默认 `backend/.env`；
  绑定 `127.0.0.1:$DAWN_PORT`，默认 8001）。

## 关键环境变量

与 FastAPI 共享的（`SECRET_KEY`、`QINIU_*`、`TENCENT_*`、`LIGHTHOUSE_INSTANCE_ID`、
`VAULT_ALIVE_URL` 等）逐字取同一 .env，**两边的默认值也必须一样**（同一个 .env 里没写的键，
两套后端得理解成同一件事）：`scripts/check_config_defaults.py` 把 `main.dawn` 的
`get_or(cfg, ...)` 表与 `app/config.py` 的 `Settings` 字段表逐键比对，只在一边有的键要在脚本里
具名豁免并写明理由，CI 的 `python-backend` job 每次跑。Dawn 专属：

- `DAWN_PORT`（默认 8001）、`DAWN_CORS_ORIGIN`（默认 `https://dawnop.com`）。
- `DAWN_DB_PATH`（默认 `backend/dawnop.db`；生产 `/opt/dawnop/data/dawnop.db`，与 FastAPI 同文件，WAL 共享）。
- `DAWN_SIMPLE_EXT`（`libsimple` 路径，无扩展名；写 `articles` 触发 FTS `tokenize='simple'` 必须加载）。
  配置非空时加载失败会拒绝启动，空串仅用于明确不使用 simple 的隔离环境。

JVM 两个连接入口共用严格初始化：实际 SQLite 版本至少 3.51.3、busy timeout=5000、
WAL 查询结果必须是 wal、配置扩展必须加载；错误关闭连接并传播到启动层。
启动及 cleanup 均先在内存连接上记录 sqlite_version()/sqlite_source_id()，
`java -jar backend-dawn.jar --engine-info` 只运行该诊断，不读取配置或打开业务库。
版本门拒绝已知旧引擎；它不能替代来源、摘要及扩展验收。
本地 S4 额外执行 native 3.54.1 → 新 JVM → native 的真实 handler 回滚矩阵，
包含保留 WAL、关闭重开、提交后与未提交时杀进程四种交接，验证文章三触发器、paste 触发器和 FTS integrity，
并核对 FTS secure-delete 保持关闭。这不是生产恢复或 S12 包验收。

共享的那批里有一个带边界：`QINIU_TOKEN_EXPIRES`（默认 3600）只收 **1..86400**（闭区间，单位秒）。
它是本后端签的每一个七牛 deadline——直传凭证、私有下载 URL、代理上传——的时效。写了但不合法
（非数字、0、负数、超过一天）**在启动时 fail closed**，不回落默认值：默认值是「没写」的意思，
拿它去兜一个写错的值，等于把打错的字变成一台跑着、且没人知道凭证活多久的服务器。判词在
`config.int_in_range`，越界与非数字各有一条 Dawn 单测和一个变异体；假七牛那侧另有
`fm.upload-token.deadline-window` 真去花掉一次凭证。

## 与 FastAPI 的已知分歧

`backend/` 冻结之后，两套的线上形状不再逐字段相同。下面这些是**看过并接受**的，不是漏掉的。
它们此前只以各自代码点上的注释存在（M6 的差分 harness 里曾有一张 `RATIONALIZED_STATUS`
登记表，改成 golden 对拍时随 harness 一起删了），所以在这里收一份。平时跑的只有 Dawn，
这些差异只在回滚到 FastAPI 的那段时间里才看得见。

- **`POST /api/fm/register` 的准入，Dawn 严格得多。** Dawn 要求 `(key, path)` 这一对在
  `pending_uploads` 账本里确有其行，且该 key 尚未被任何 `files` 行引用，任一条不满足都是 409。
  预检在碰七牛之前跑（`repo_fm.validate_register_preflight`），最终写入的即时事务里再验一遍
  （`repo_fm.finalize_register`），账本行按 `(key, path)` 消费并断言恰好消费一行。FastAPI
  （`backend/app/api/fm/uploads.py`）只校验 `path`/`key` 非空、`stat(key)` 成功，然后**按 key
  单独**删账本行，既不比对账本里记的 path，也不看这个 key 有没有被别的行占着。
  于是同一个请求 Dawn 回 409、FastAPI 回 200：把一次成功过的 `register` 换个 path 重放，
  FastAPI 会再建一行（`files.key` 上没有唯一约束），结果是**两行元数据指向同一个七牛对象**。
  这不是纸面问题。FastAPI 的 `/api/fm/delete` 不做引用计数，删掉其中一行就把对象删了，
  另一行成为指向已删对象的悬空引用；而这行落在两套共用的那个库里，回切到 Dawn 之后仍在。
  Dawn 的回收是引用感知的（`svc/files.gc_unreferenced` 在即时事务里查 `files` 与
  `pending_uploads`），所以它既造不出这个状态，回切后也不会把它踩坏。
  够不够得着取决于回滚方式：**受守护的回滚**（哨兵 `/etc/dawnop/fastapi-file-routes-disabled`，
  `backend/app/core/process_guard.py`）把整个 `/api/fm` 关成 503，这条路径够不着；
  手工拉起一个不带哨兵的 uvicorn 就够得着。回滚脚本会放哨兵，手起的进程不会。
  **不要为此去改 `backend/`**：那棵树是冻结的回滚目标，给它加准入是没人要的行为变更。
  没有 golden 钉住这道门（假七牛那套录的是 `register` 的成功、对象不存在、对端不响应三条），
  钉住它的是 `test/jvm/repo/repo_fm.dawn` 里那两条单测。

- **缺查询参数 `path` 时的状态码。** `GET /api/fm/{sign,content,preview,download}` 完全不带
  `path` 时，Dawn 的 `qparam()` 把缺失参数取成空串、落到「文件不存在」的 404；FastAPI 把它声明成
  必填 `Query(...)`，在进 handler 之前就 422。两边都是拒绝、都不改任何东西，前端只认状态码。
  用例上的注释在 `scripts/contract_edge.py`。

- **未鉴权时 `detail` 的文案。** Dawn 的每一个 401 都是 `{"detail": "无效或过期的凭证"}`。FastAPI
  在**完全没有 Authorization 头**时由 `OAuth2PasswordBearer` 自己答掉，文案是 `Not authenticated`；
  头在但无效，才走到 `backend/app/deps.py` 里同样那句中文。状态码两边都是 401。

- **路径参数不是整数时的 422 文案。** 查询参数那批（`page`/`size` 的类型与范围）Dawn 逐字复刻了
  pydantic 的英文句子，两边一致；路径参数不是这样：`GET /api/pages/admin/abc` 在 Dawn 是
  `page_id must be an integer`，FastAPI 会是 `page_id：Input should be a valid integer, ...`。
  状态码都是 422。

- **`fm_split` 的寻址是无损的。** Dawn 的 `full` 与 `fm_split` 互为逆，`"a.txt "` 与 `"a.txt"` 是
  两个地址，指不到行就 404；FastAPI 冻结的 `_split` 末尾仍是 `.strip()`，两个拼写会塌到同一行，
  这正是「删 a.txt 删掉了 a.txt」的来源。理由写在 `core/src/util/paths.dawn`。

- **`QINIU_TOKEN_EXPIRES` 的边界只有 Dawn 有**，见上面「关键环境变量」。FastAPI 那边只有 pydantic
  的类型检查，`0`、负数、`36000000` 它都收下。两套读同一份 .env，所以回滚会把一个让 Dawn 拒绝启动
  的值变成一台跑着的服务器。

## 模块地图

S10 保留独立的 core、JVM/native app 和 test 根。core 有 65 个模块，持有全部路由、业务、
配置装配与迁移接口；两个入口通过路径依赖消费同一份 application.app_routes。
JVM 只留 6 个模块（入口、JDBC、HTTP 适配及测试构造），native 有 6 个 Dawn 模块及 fd teardown C 接缝。
测试仍在一次性 JVM 同模块镜像合并白盒片段；core 自身和 native 完整依赖图分别接受零 Java 扫描。

- `core/src/db/runtime.dawn` 的 DbConfig 保存配置与纯 Dawn DbOps 工厂，泛型 with_db 在操作记录
  的 bracket 内安装 Db 六臂。JVM 工厂每工作单元开关真实 JDBC；native 外层 sqlite.with_db 拥有
  单交换唯一连接，借用操作只在该动态作用域内执行，不扩展既有写事务。
- `core/src/util/http_runtime.dawn` 保存四种出站形状和显式 batch 的 IO 回调；两端
  util/application_http 适配实际 owner，共享路由实际执行时安装 Upstream/UpstreamBatch。
- `core/src/util/runtime_ttl.dawn` 保存五槽 SQLite 地址；JVM util/ttl 只为既有测试提供路径构造。
- `core/src/application.dawn` 统一环境默认值和路由顺序，两个入口配置文件均只加载一次。
- `core/src/backup.dawn`：维护命令 `--backup <dest>` 的共享版本门、VACUUM INTO 快照与副本
  integrity/FTS external-content 校验。专用连接先用内存库拒绝 <3.51.3，再校验源连接，
  不 configure、不 migrate、不请求切换 WAL，配置 tokenizer 加载失败即拒绝。
  `scripts/backup-engine/runtime.py` 构建真两臂、固定 SQLite/simple 与旧引擎负控夹具；
  根目录 `scripts/backup_db_mutants.py --work <空目录>` 只改哨兵副本并验证先红后绿。
  `backend/tests/test_backup_db.py` 的真脚本/真库判词自动跑 JVM/native，生产安装见
  [`deploy/README.md`](../deploy/README.md#五库备份)。
- `core/src/maintenance.dawn` 显式 migrate、check_schema、cleanup。serve 路径不建表；
  native 只核对已有 WAL 和 additive schema，未准备的库在写出 wire 前失败。

以下模块名按原相对路径记：业务模块均在 core/src 下，适配器在各自 app 根下，
对应测试在 test/core 或 test/jvm。main 属于 app，config 属于 core。
`qiniu_*`/`tencent_*` 进目录后丢前缀
（`qiniu/sign`、`tencent/client`），`api_*`/`repo_*` 保留前缀——api 与 repo 是唯一会同名对撞的两层。

**基础设施（预备刀 1–5）**
- `app/native/src/db/native_sqlite.dawn` — sqlite 0.1.0 的连接 owner 与 Db 六臂。
  `with_db(path, ext, body)` 返回 Result[T, String]；每次先装 busy_timeout=5000，
  查询并核对 WAL，再按需用固定 sqlite3_simple_init 的 C 接口加载扩展；失败返回调用层，
  不执行 body。SQL 扩展 gate 始终关闭。NULL、列名和重复 label、Bool、SQL quote row_sig
  与 core 事务规则保持；不把 sqlite3_changes 用于 DDL 或编辑检测。版本和源码树摘要
  固定到工具链同一个 v0.99.0 URL/subdir。详见 `docs/native-sqlite-design.md`（仓库根）。
- `core/src/db/sql.dawn` — 站点 `Db` 效果与 `SqlV/Col/Cell/Row/QueryResult`：
  六个操作为 db_query、execute、execute_changes、last_id、busy_timeout、in_transaction，
  均返回 Result[..., String]。query/query_rows、exec、scalar_int、require_tx、row_sig 与普通泛型
  事务函数在 core 组合这些操作；业务使用隐含的当前连接并声明 !Db。
  query_rows 在 core 从完整列名建立索引，同名列后者覆盖；空结果的 QueryResult 仍保留列名。
- `app/jvm/src/db/jdbc.dawn` — 唯一 JDBC owner，不透明 DbConn 仅供适配层和 JVM 测试使用。
  with_connection 在连接 bracket 内执行 with_jdbc，实际回调执行时才安装 Db，连接与语句正常及异常
  路径均关闭。ForeignError 统一经过 catch_text，Result 错误文本保持原状。
  **事务状态由 SQLite 回答**：JDBC autoCommit 标志看不见裸 begin immediate。adapter 用 begin
  探测并立即撤销自己的空事务，嵌套拒绝则保留外层；core 的 with_tx 和 require_tx 读 in_transaction。
  with_immediate_tx 直接读 begin immediate 的拒绝，不额外探测。两种入口都拒绝嵌套，不清外层；
  Err、panic 和提交失败都会释放本次事务。with_tx 仍无生产调用方，改用可移植的 SQL begin。
  负控在 scripts/db-connection-boundary-mutants：移除真实 handler 必须编译失败，后者覆盖改前者、
  autoCommit 误判、清外层与错误文案变异均真实编译并核对专属红集。
  **按列名读行（`query_rows` + `row.col_int("id")`）只用在选择列表是共享常量的三处**：
  `repo_article` 的 `LIST_COLS`(12 列)、`repo_page` 的 `PAGE_COLS`(11 列)、`repo_viz` 的
  `OUT_COLS`(8 列)——这类列表被多条查询共用，加一列就得数所有下标，位置索引在这里是负担。
  其余仓（tag / fm / export / auth / search）是 1–4 列的就地 select，下标与 select 同屏可见，
  按位置读更短也不易错，**故意保留**，不是漏迁。
- `util/ferr.dawn` — `ForeignError` → 旧版 `Throwable.toString()` 文本（`fe_text`）：v0.33.0+ 的
  `catch_fault`/`cast` 返回结构化 `ForeignError`，各屏障模块经它转回 `Result[T, String]`，错误文案与升级前逐字节一致。
- `util/errkind.dawn` — 一条 `Result[T, String]` 的失败到底该出什么状态码：`Kind` ADT
  （`KPlain`/`KConflict`/`KPrecondition`/`KInvalid`/`KUpstream`/`KTimeout` → 沿用调用点默认 / 409 / 412 /
  400 / 502 / 504）。生产者用 `conflict/precondition/invalid/upstream/timeout` 打标，API 边缘用
  `split_kind` 拆开。标签走 `U+0001` 前缀而不是另开一个类型：这样**线上的文案与生产者写的逐字节一致**，
  迁过来时没有一个端点要改措辞。从前是在 API 层匹配中文子串（「已存在」「所属页面」「七牛」）反推，
  那是一份没人声明的跨层契约，改一句话就悄悄改了状态码——并且已经错过一次（`qiniu/rs` 写的是
  `qiniu` 不是「七牛」，于是被对象存储拒掉的 WebDAV COPY 回 500，而 FastAPI 参照回 502）。
- `app/jvm/src/db/db.dawn` — JVM DbOps 工厂与测试用旧路径构造；每个业务工作单元一连接，WAL、5000ms busy timeout、扩展错误均到达调用边界。
- `util/bcrypt.dawn` — 纯 Dawn 的 bcrypt（`$2a$`）校验，取代 jBCrypt；状态是 `std/mem` 的 `I64Buf`（`with_mem` 内生灭，对外纯函数）；cost 12 校验与 jBCrypt 同量级（JVM 约 0.16 s），换 cost 前先重测。
- `util/crypto.dawn` — sha256 / hmac-sha1 / hmac-sha256 / base64url / uuid（auth、七牛、JWT、腾讯共用）。
- `core/src/util/http.dawn` — 可移植出站类型、效果与错误策略：`fetch/post` + `fetch_bytes`（二进制体，G6）；
  **出站 HTTP 是具名效果 `Upstream`**（四个操作：text / bytes / stream / 起一个不等的交换）。
  调用方拼一个纯 Dawn 的 `OutReq` 交给装好的 handler，只有臂里才碰 java.net.http；生产臂是
  `app/jvm/src/util/http_jvm.dawn` 的 `with_upstream(cell, body)`，测试臂按表作答。**切口刻意开在错误面之下**：臂交回的是原始
  `ForeignError` 而不是成文的消息，于是 `deadline_text` 的措辞、`timed_out` 的判词、
  `as_outbound` 的 504/502 分流在测试里跑的仍是生产代码——一条答 `HttpTimeoutException` 的臂，
  微秒内断言完从前要停机十秒才断言得到的那三行。`Pending` 里装的是「交付回复的 thunk」而不是
  future，所以 `await_resp` 是 `!io` 不是 `!Upstream`（证据在起交换时就花掉了），而想看 await
  的 handler 看自己造的那个 thunk 即可，不必为「被观察」另开一个操作。
  生产的安装点只有**四处**，都在最内层拥有一个完整出站工作单元的边界上：`api_fm` 的
  `guarded_out`（`guarded` 里再套一层 handler，八条会出站的路由改调它）、`api/webdav` 的
  `dav_handler`（一处罩住 GET/PUT/DELETE/MOVE/COPY 全部）、`api_monitor` 的路由闭包
  （issue 与 read 是两次调用，必须同一个 handler 罩住）、`api_drop` 的 `public`（上传链接的四条
  公开路由，连同 Clock 一起装）。其余 `with_upstream` 都在测试里。
  **必须在回调执行时安装**：`with handle` 只给自己块的剩余部分供证据，路由闭包不捕获它，而
  web 的 `Handler` 别名是闭合行（`!io`），发 `!Upstream` 的闭包根本不是 `Handler`——标准 Clock/Fs 同样在回调的工作边界安装。`db/db` 的 `with_db` 与 core `db/sql` 的 `with_immediate_tx` 使用效果多态（`!e`）：它们是
  纯转发器，闭在 `!io` 上会挡住「在写事务里做一次出站调用」这件生产每天都在做的事。
  故意留在 `!io` 的三处：`new_client`（它造的正是臂用的客户端）、`file_body`（只装配请求体）、
  `close_stream`（放掉臂已经交出来的流）——都不碰对端，也都没有值得注入的断言。
  **每个出站请求都带单请求超时**。`request_for` 是全模块唯一装配请求的地方，也是唯一一处 `.timeout(`，
  新加的 body handler 想漏掉超时都没有位置。分两档：管理类调用 `MANAGEMENT_TIMEOUT_S` = 10s
  （七牛 rs stat/delete/copy、用量与 CDN、腾讯云、vault 探活），搬字节的调用
  `TRANSFER_TIMEOUT_S` = 3600s（代理上传、WebDAV PUT、下载代理）。
  之所以必须分档：实测（JDK 26）这个超时是**整次交换的硬期限**，不是空闲计时器——稳定推进的响应体
  照样在期限处被切断，POST 请求体上传到一半也一样，而且期限还覆盖 connect 阶段。于是管理档要短
  （`svc/files.dawn` 的 gc 刻意把 `delete_obj` 关在 `BEGIN IMMEDIATE` 里防 TOCTOU，这个值就是
  SQLite 全局写锁被占的上界），传输档必须远高于任何合法传输，因为它是时长上限而不是延迟上限。
  超时的错误文本写明超的是哪一档（`outbound HTTP timed out after Ns: ...`），**其余传输错误逐字节不变**，
  并且**只判一次**：`deadline_text` 是唯一认定「这是超时」的地方，`timed_out` 读的是它写下的
  `TIMEOUT_PREFIX`，所以文案说超时、状态码说别的这种分歧无处产生。分类由 `as_outbound` /
  `tag_outbound` 落成 errkind 标签：超时是 `KTimeout` → **504**，其余出站失败（含七牛的拒绝）
  仍是 `KUpstream` → 502。这条分界对着屏幕的人有意义：502 读作「凭据或端点不对」，504 读作
  「对端没响应，可以重试」。落到 wire 上的证据在 qiniu golden 的 `fm.register.quiet_peer`
  （假七牛接受连接后不作答，`/register` 的 stat 走完管理档预算后回 504）。
  **出站客户端是全进程一个**（JVM owner 的 `HttpCell`，main 建一次、单独传入四处路由边界；
  `Auth` 只持配置，不再携带客户端）：连接池长在实例上，每次调用新建一个等于每次出站重握手（对七牛还要重做 TLS）。
  判词不是「源码里只有一处 new」——那句话在旧的 `shared_client` 每次调用都 build 时也成立——
  而是假服务器把**客户端自己的源端口**回给测试：同一个 cell 的两次请求端口相同，另起一个客户端不同。
  负控在 `scripts/http-timeout-mutants/`（10 个变异体，覆盖期限 / 超时分类 / 共享客户端三组规则）。
  自 `Upstream` 落地起归属分成两半：关于**套接字**的（期限有没有真上到线上、一个 cell 是不是一条连接）
  仍由停顿的回环对端持有，那些是计时断言；关于**代码**的（哪一档预算、怎么措辞怎么分类）由装 handler
  的断言持有，微秒级。`transfer-tier-on-management` 就是跨过这条线的那个：从前只有等满十秒才抓得到，
  现在两条 handler 断言直接读请求上的 `budget_s`，其中一条是 `svc/files` 的写事务窗口测试。
  假服务器接受连接后先停住再迟迟作答（有限停顿，所以变异体是转红而不是把门禁挂死），
  `inflate-deadline` 保留 `.timeout(` 只把值改荒谬，`client-per-call` 保留唯一那处 builder 只让它
  每次调用跑一遍，`timeout-tag-second-opinion` 给标签另开一套判据——三个都只有真跑才判得出来。
  `RequestBody` 不透明包装纯数据 `FileParts { head, file: FileSpan { path, offset, length }, tail }`，
  `OutBody.FileBody` 替代 PublishedBody。file_body 只预检文件大小，不创建 publisher；JVM owner 在
  request_for 内把完整文件描述转换成 ofFile/concat，保持原流式发送。当前 JVM 明确拒绝非完整文件范围；
  拒绝返回 ForeignError 并经原 502 分类，S8 可直接使用描述中的 offset/length。`ResponseStream` 不透明包装 web8 BodySource，
  JVM 臂先用 input_source 转换，core stream_response 再交 web 泵；转交不读源，读错误保留到消费时。
  HEAD/无体状态、成功、失败/断连由 web bracket 关闭；非2xx拒绝由 close_stream 关闭。
  response-stream 原七项一对一保留，再加错误被吞为 EOF 的第八项，共九组161变异。
  详见根目录 `docs/upstream-types-design.md`。
  FFI 门禁只约束显式 `InputStream` 源码名，不阻止 Java 返回类型由推断得到；响应侧另以
  `Stream`/`ResponseBody` 与 `streaming` 两个独立 seam 门禁阻止业务模块绕过 owner。扫描时忽略
  普通、三引号与 raw string 的文本，但 `$name` 和 `${expr}` 插值表达式仍按 Dawn 代码检查。
- **文件系统是 std 的具名效果 `Fs`**（`std/io` 自 dawn 0.72.0 起，`read_file` 等由 `!io` 改为 `!Fs`），
  形状与上面的 `Upstream` 同款：声明 + `with_fs_real` 生产 handler，没有别的语言支持。
  本仓的安装点都在最内层拥有一个完整文件工作单元的边界上：`main.dawn` 罩住
  `config.load`（整个进程唯一一次启动期读文件），`api/api_monitor` 的路由闭包罩住 `server_block`
  （读 /proc 的那一段；同样装不进 main，理由与 `Upstream` 那条一样）。
  core util/http.file_body 另在文件描述创建时装 Fs 并查询真实长度。其余 `with_fs_real` 都在测试里，服务器的安装点位于依赖包 `web_jvm/server`。
- **环境变量是 std 的具名效果 `Env`**（`io.getenv` 自 dawn 0.76.0 起为 `!Env`；0.78.0 起 `io` 是唯一的环境效果，
  其余效果必须写在签名上）。本仓只在启动期读两处环境变量：`config.lookup` 与 `main.env_or`，
  各自就地罩一层 `io.with_env_real`，签名保持 `!io`（调用方本来都是 `!io`，启动期读取也没有测试替身要装）。
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
  `load` 是 `!Fs` 不是 `!io`：它唯一的对外接触就是那次读。
- `web` — **dawn-lang 的 `packages/web`**（`[deps.web]` url+hash 依赖，vendored 副本已删）：`server`（HttpServer + G6 二进制响应体 + 流式）/`router`（tags/任意动词）/`types`/`middleware`（logging/cors/body-limit）。

**鉴权（刀 7）**
- `core/src/svc/dav_auth.dawn` — WebDAV 的 HMAC 凭据键、300s 成功缓存、账号状态/密码版本联查与
  SQLite 短租约。密钥复用 `.env` 的 SECRET_KEY，域分离；轮换须替换全部共库进程。
  `auth_account_state` 缺行默认启用，`enabled=0` 停用 WebDAV；直接改 users.password_hash 也立即失效。
  随机 owner token 与 5s 到期共同约束发布，bcrypt 在事务外；失败仅在短租约内合并，等待上限 15s。
  详见根目录 `docs/auth-cache-design.md`；`scripts/auth-cache/run.sh` 用同源 JDBC/native 场景、
  40 进程真实 cost12 bcrypt、跨臂命中、崩溃恢复与六个先红负控验收。
- `svc/auth.dawn` — `Auth` 配置类型、`current_user`（Bearer + `?token=`）、login/me；bcrypt 校验走 `util/bcrypt`（`$2b$→$2a$` 归一）。
  **Authorization 头的形状由 `scheme_param` 一处说了算**（Bearer 与 WebDAV 的 Basic 共用它）：
  在**第一个空格**处切开，scheme 大小写不敏感地比，其余部分**原样**取走。三条推论都与 FastAPI
  的 `get_authorization_scheme_param` 一致：没有空格就是「全是 scheme、没有凭据」，所以裸 token
  （`Authorization: eyJ...`）不认证；`bearer` 与 `BEARER` 都认证；`Bearer  x` 的凭据是 " x"，
  于是验签失败。**前两条以前是反的**（#270，2026-08-15 修）：旧代码精确匹配 `"Bearer "`，
  不匹配就把整个头当 token，于是 Dawn 收下 FastAPI 拒绝的形状、又拒绝 FastAPI 收下的形状。
  负控在 `scripts/auth-header-mutants/`（4 个变异体，一条规则一个，各自唯一转红），
  wire 侧在 edge golden 的 `me.schemeless` / `me.lowerScheme` / `me.upperScheme` 等六条——
  其中只有这三条能分辨新旧，另外三条（`me.wrongScheme` / `me.twoSpaces` / `me.emptyScheme`）
  新旧都回 401、只是理由不同，钉住它们的是单测和变异体。
- `core/src/util/jwt.dawn` — HS256 签发/校验，与 PyJWT 双向互认，S2 随 Clock 迁入 core。

**公开只读（刀 6/8）**
- `api/api_public.dawn` — health/search/viz/`{slug}`/articles/pages/tags 路由。
- `repo/repo_article.dawn` / `repo/repo_page.dawn` / `repo/repo_tag.dawn` — 只读查询 + `word_count`/分页/标签。

**后台写（刀 9/10）**
- `api/api_articles.dawn` — 文章 CRUD + Markdown 导出。
- 全局设置的后台分页/密度、文件参数、上传链接创建默认值见 [`../docs/admin-settings.md`](../docs/admin-settings.md)；
  `scripts/check-settings.py` 在 CI 验证部分更新、事务回滚、并发约束与持久化。
- `api/api_settings.dawn` / `api/api_tags.dawn` / `api/api_pages.dawn` / `api/api_viz.dawn` — 一文件一后台页面。
  （曾是一个 `api_admin2.dawn`：它不是从 `api_admin` 拆出来的，是刀 9 的新功能进了新文件、
  名字随手叫了 `2`，于是文件名记的是写作顺序而非内容。2026-07-19 按内部已有的分区注释拆开。）
- `repo/repo_write.dawn` / `repo/repo_pagetag.dawn` / `repo/repo_viz.dawn` / `repo/repo_settings.dawn` — 写模型（唯一 slug、标签解析、校验、reorder）。
- `util/slugify.dawn` / `svc/export.dawn` — slug 生成（对齐 `core/slug.py`）/ Markdown frontmatter 导出。
- `svc/search.dawn` — FTS5 排序委托同一 SQL bm25，仅高亮在 Dawn 侧重写。

**文件管理（刀 11）**
- `api/api_fm.dawn` — 共 17 个端点，`upload` 之外的 16 个是：列目录 / sign / 预览 / 下载（302）/
  内容代理（二进制）/ stats / search / CRUD / save / create-file / register / upload-token。
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
- `qiniu/creds.dawn` — `Qiniu` 凭据与端点记录（ak/sk/bucket/domain/expires/rs_host/up_host）。
  sign/rs/stats 的函数整条收它，而不是收一排靠位置对齐的 ak/sk/bucket 标量。
- `qiniu/sign.dawn` — 三类七牛签名：上传凭证、私有下载 URL、QBox 管理、QiniuMacAuth（统计/CDN/账单，含 body）。
- `core/src/qiniu/rs.dawn` — 管理 REST：stat/delete/copy/upload_text/upload_bytes/upload_file。
- `util/paths.dawn` / `repo/repo_fm.dawn` — 路径原语 / 虚拟树（path↔key，DirEntry 序列化）。
- `util/multipart.dawn` — 入站 `multipart/form-data` 解析，只服务 `POST /api/fm/upload`（代理上传）。
  结构在**字节层**定位（`byte_index_of`/`byte_slice`），每个 part 的正文原样留作 `Bytes`，
  只有 ASCII 头块解成字符串取字段：文件字节不经过一次 String 往返，二进制上传才不会被改写。

**前端动态路由的存在性检查**
- `api/api_public.site_route` — `/api/site-route/{kind}/{slug}`，存在 204，不存在 403（Nginx `auth_request` 的拒绝约定，浏览器主请求转为 404）；
  kind 为 `article` / `p` / `tag`，文章只允许已发布或携管理员鉴权的草稿。只读，不增加阅读数。
  Nginx 用内部子请求判断动态路由，未知路径与缺失资源以 HTTP 404 加载前端提示页，服务故障保留 5xx。

**上传链接（drop）**
- `api/api_drop.dawn` — 两扇门。`/api/fm/drops*` 是管理端（`guarded`，管理员 JWT）：建、列、吊销、删。
  `/api/drop*` 是匿名上传方：token **只从 `X-Drop-Token` 头读**（`drop_token`），不读 query（会进访问
  日志）、不读 Authorization（那是管理员的头，两种凭据不能互相顶替）、不读 cookie；头出现两次是 400。
  反过来，`guarded`/`guarded_flexible`/WebDAV Basic 都不看这个头，drop token 当 JWT 或 Basic 密码用
  一律 401（`drop.token.not-admin`）。Clock 与 Upstream 的生产 handler 装在 `public` 路由闭包里，
  理由使用 std Clock。给上传方的回包只有 `{name, size}`（upload-token 必须回 `key`，客户端要拿它直传），
  不回存储路径和任何别的文件（`drop.responses.minimal`）。
- `svc/drop.dawn` — 规则全在这。①每个公开请求都从库里重新判一遍：存在、未吊销、未过期（Clock 的 now）、
  额度没满、目录还在且是目录（`status_of`，吊销 > 过期 > 用尽 > 目录没了）；未知 404，其余 410。
  ②名字只当**单个路径段**（`name_refusal`：空、`.`/`..`、`/` 或 `\`、C0/DEL/C1 控制字符、超 255 字节，
  最后再过一遍 `util/paths.name_verdict`）；点号这条与写入守卫各有一份是刻意的，两边的变异体要能各自
  唯一归属。路径由服务端 `fm_join(drop.dir, name)` 拼，同名取 `name (1).ext`…（`candidate_name`），
  在写入事务里再确认一次。③直传：upload-token 在一个即时事务里挑名字并同时写 `drop_pending`（register
  唯一认的账本）与 `pending_uploads`（孤儿治理），凭证 `scope=bucket:key`、`insertOnly=1`、
  `fsizeLimit=min(单文件上限, 剩余字节)`、deadline ≤ 1 小时且不晚于链接过期（`direct_deadline`）。
  register 只收 key，按 `(key, drop_id)` 查账本，别的 drop 签的、管理端签的、编的一律 404；path 取自账本，
  stat 以七牛 fsize/mimeType 为准再过 `safe_persisted_mime`。④一步式 PUT：路由带 `stream-body`，
  请求体由框架落盘、处理完删除；没有 Content-Length 或带 Transfer-Encoding 回 411，声明长度超额度回 413，
  落盘大小与声明不符回 400。前四条（连同 token 与链接状态）是 `put_precheck`，路由 guard 在读体之前跑它
  （`api_drop.put_guard`），处理函数再跑一遍，两处状态码与文案不会分叉；路由 `body_limit` 是
  `MAX_MAX_FILE_BYTES`（5GiB），挡住声明长度说谎、流中途越界的体。⑤两条路共用 `commit_tx`：在 `with_immediate_tx` 里重判链接、复核账本行
  （防重放）、按真实大小复核额度、挑名字、插行（普通 insert，从不 upsert）、消费账本、扣额度；所有检查
  在第一次写之前，拒绝时提交的是空事务。被拒的上传删掉七牛对象（`discard`：先删账本行，再走引用感知 GC）。
  **读体之前拒绝**：web 5.1 之前，框架对 `stream-body` 路由是先把整个请求体落盘、再进处理函数
  （dawn-lang#310），落盘大小只能靠 nginx 封顶。现在靠路由 guard 与 `body_limit` 在后端做到；
  契约 `drop.put.refused-unread` 用「声明 100MB、只发几个字节」的请求钉住（不读体才能在超时内答复）。
- `repo/repo_drop.dawn` — `upload_drops`（完整 token 供管理员再次分享，鉴权查 sha256）与 `drop_pending` 两张表的 SQL，以及
  `ensure_schema`。建表和旧 schema 升级由显式 --migrate 执行，请求不重复初始化。
  恢复数据库后须重新 prepare；契约 harness 在重播 fixture 时显式迁移或恢复已迁移快照。旧的仅摘要链接
  在升级事务内清除，同时清其 `drop_pending`；已上传文件与 `pending_uploads` 孤儿账本保留。
- `qiniu/sign.upload_token_limited` 与 `util/crypto.random_token`（32 字节 `Random` 效果取的随机字节，base64url 43 字符）
  是它新加的两块地基；管理端的 `upload_token` 逐字节不变。

**公开 Markdown 与私人记录**
- `api/api_paste.dawn` — `/api/pastes` 的公开列表/搜索/创建、详情/原文和正文上限配置；
  `/api/pastes/admin*` 的列表/统计/查看/删除/保留/清理/额度设置全部要求管理员 JWT。
- `svc/paste.dawn` — Markdown/Unicode/UTF-8 字节边界，纯 IP 字面量解析（IPv6 /64），
  域分离 HMAC 与按 UTC 日/分钟的来源及全站持久额度；容量、扣额、插入在一个即时事务内。
- `repo/repo_paste.dawn` — `pastes`、`paste_budgets`、`paste_settings` 与 FTS5 trigram/同步触发器；
  列表仅取摘要，到期内容立即从公开读取中排除。存在性探测不取正文、不记阅读数据。
- `main --cleanup-pastes` 与 `deploy/dawnop-paste-cleanup.{service,timer}` — 每小时物理清理到期正文和
  超期预算，和创建时/管理员清理共用 SQL，不退还当日额度。首次部署需安装并启用定时器。
- `scripts/check-paste.py` — 隔离 JVM/SQLite 的公开与管理权限、输入边界、来源/全站/容量额度、
  IPv6、并发、重启、删除/过期及 CLI 清理验证；CI backend job 在发布 artifact 前执行。
- 私人记录复用现有 `/api/fm/create-file` 和 `/api/fm/save`，七牛私有正文不进入公开 Paste。
  产品与部署细节见 [`../docs/paste.md`](../docs/paste.md)。

**监控（刀 12，S9 显式批次）**
- `api/api_monitor.dawn` — `/api/monitor`，120s 聚合缓存、300s billing 缓存及实时 quota 注入。
- `core/src/svc/monitor_batch.dawn` — 请求列表 → 批量执行 → 稳定 ID 回填。第一轮包含
  Tencent 三项、Kodo 四项、domains、冷 billing 两项和 vault 两项；第二轮为依赖 domains
  的 flux/bandwidth。失败保留在各自组的 error 字段，空 domains 不发第二轮。
  vault latency 使用 alive 项自身单调耗时；冷 CPU 窗口另加300ms，不能声称与 Proc.run 重叠。
- `core/src/util/http_batch.dawn` — 相邻效果 UpstreamBatch；每项保留 ID、exit、status、
  headers、body、elapsed_ms、ForeignError。重复输入 ID 拒绝，缺失或重复结果 ID 拒绝。
- `app/native/src/util/http_curl.dawn` — 每轮一次 curl --parallel，逐项 write-out 关联私有文件，
  不依赖 curl 总退出码。复用 S8 argv、私有配置、错误脱敏和大小边界，完成后全部清理。
- `app/jvm/src/util/http_jvm.dawn` — 现有 HttpClient sendAsync；成功与失败各用非空参数的
  completion callback 记录单项完成时刻，再按 ID 收齐，等待顺序不改变 latency。
- `scripts/monitor-batch/run.sh` — 同一 consumer 双臂验证六项延时并行、乱序、部分失败和超时、
  两轮依赖、各组隔离及缓存；先编译真实源码负控再要求指定判词红。
- `svc/monitor.dawn` — 本机 /proc、CPU 快照和旧单块兼容入口；路由的出站已交给 core 批次。CPU 为本次减上次持久样本，首次、过期或
  无效差值回落300ms窗口；每次请求都刷新，聚合缓存命中仍照采。
  #20 测试用注入的计数器与 CpuWait 数等待次数，第二次调用不新开窗口，无墙钟上界。
  cpu-sampling 原四变异与 owner 矩阵不变，always-sample-a-window 仍只击中原 owner。
- `core/src/tencent/client.dawn` / `tencent/sign.dawn` — 腾讯云请求数据与 TC3 签名；
  `core/src/qiniu/stats.dawn` — 共享请求装配和响应解码，旧单请求调用继续复用。
- `core/src/util/clock.dawn` — 墙钟 ns 除以 1000000 得毫秒，进程内耗时只读 std Instant，
  sleep_ms 通过 std sleep_ns 交给安装的 Clock。JVM 批次在完成回调记录单项单调时长。
- `core/src/util/fs.dawn` — std file_size 的错误转旧 String 通道；目录、缺失与权限失败均保留 Err，
  WebDAV 仍 500，drop 仍 Failed，真实空文件才返回 Ok(0)。monitor disk_space 取 available 对齐旧 usableSpace。
  `scripts/clock-fs/run.sh` 先证明千倍换算、墙钟用于耗时与目录大小当成功三个源码负控会红，再验 JVM/native。
  `test/sql/src/fake_clock.dawn` 共用假钟以 scoped Mem 记录 wall/mono、sleep 次数与累计 ns，
  sleep 推进两钟，单独回拨 wall 不影响 elapsed；CPU 与五槽 TTL 边界测试不真实等待。
- `core/src/util/cache.dawn` — `runtime_cache(namespace, cache_key, payload, created_ms)`，
  `(namespace, cache_key)` 复合主键；仅通过 `!Db` 与 std `Clock` 存取。monitor 120s、
  respack 300s（monitor/FM 共享）、space 600s、CPU 样本 300s，分别为 `.v1` 命名空间。
  dav 300s 改由上述认证协议持有 `dav.auth.v2`，初始化清除旧 `dav.v1` 摘要槽。
  命中只读、不续期，毫秒边界严格小于 TTL，时钟早于写入时间不命中；写入只持短事务且要求显式迁移已完成。
  CPU 每次请求回写，持久化 boot_id，启动身份不符、计数回退、样本过期继续走 300ms 窗口。
- `app/jvm/src/util/ttl.dawn` — SQLite 缓存的 JDBC/Clock 边界，`TtlCell` 仅持地址。
  启动兼容加表；旧 JVM 可忽略，不改变原业务表。WebDAV 不再通过此包装读写成功缓存。
- `scripts/ttl-cache/run.sh` — 同一 test/sql 场景双臂、两新进程四种组合互读、写锁下只读命中，
  以及 TTL、namespace、倒退、命中写库、CPU boot 和真实退回 AtomicReference 的先红负控。

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
