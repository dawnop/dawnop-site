# K2 维护与后续运维清单

本刀仅交付代码和合成证据。以下服务器命令是待另单批准的占位 runbook，本次未执行。
K2 的代码门与生产门分别计算，服务器副本、真实源、客户端、实际资源和 S13 未完成时，
`production_gate=BLOCKED`。本刀不安装包、不接触真实桶、不启动七天观察。

## 维护入口与默认值

`backend-dawn/scripts/representation_backfill.py` 只编排身份、私有文件、预算和收据。
`app/native --representation-backfill <private-input>` 在读取 dotenv 前进入维护分支，
共享 `core/src/representation_backfill.dawn` 的 Db/Upstream 和下载 HEAD 校验器。
不启动 HTTP，不扫描 bucket，不读对象正文，不自动迁移 schema。

调用者必须显式提供目标 DB、排除的 live DB、身份文件、0700 工作目录、候选二进制、
0600 配置。配置仅有 `ak/sk/bucket/domain/rs_host` 和可选 `ca_bundle`；不得复制开发者 env。
普通模式角色为 replica，拒绝 live 的相同路径、realpath 和硬链接，以及受保护系统目录。
身份绑定 realpath/device/inode/role。私有输出不得覆盖任何输入或 DB 别名。
`--init-identity` 只创建身份文件，不进行回填。

默认阶段是 dry-run；page-size=100、interval-ms=500、max-requests=10000、max-seconds=3600。
单并发，每个待处理文件最多两笔请求：stat 仅校核存在性/长度，下载 identity HEAD 提供验证器。
每笔网络预算 10 秒；请求前预留两笔网络及两次限速等待的时间预算。外层另限制子进程总时间并清理整个私有进程组。
SQLite 必须满足既有 O1 引擎门。默认使用包的系统 loader；测试或经审核的环境可用
`--sqlite-library <private-directory>/libsqlite3.so.0` 显式选择引擎，绝不继承调用者的 loader 配置。
收据绑定实际 SQLite 版本及 source 指纹；换引擎必须重新演练。

阶段语义：inventory/verify 仅全量扫描，dry-run 允许探源但 SQLite query_only，apply/resume
才可写。id keyset 分页，网络在事务外，单行短事务 CAS 比较 id/path/key/is_dir/旧 size/
updated_at/content_type/全部旧表示字段。仅表示字段及实际完整 size 可变；逻辑日期、路径、
目录、认证、pending、配额、FTS 不变。resume 先读私有高水位，再从零重扫；最后全量约束扫描
决定 ready，不以游标代替覆盖率。缺 key、负数、重复 key、未扫行、缺值、冲突或错误均阻止 ready。

## 副本命令模板

所有大写路径和预算均为占位符。执行者须先批准规模、速率和失败中止线，并确认现有引擎备份
及 additive migration 已在服务器隔离副本完成。不要把真实参数、行、日志或收据发回聊天。

```sh
python3 backend-dawn/scripts/representation_backfill.py \
  --db REPLICA_DB --live-db EXCLUDED_LIVE_DB --identity PRIVATE_IDENTITY \
  --work PRIVATE_WORK --binary CANDIDATE_BINARY --config PRIVATE_CONFIG \
  --init-identity

python3 backend-dawn/scripts/representation_backfill.py \
  --db REPLICA_DB --live-db EXCLUDED_LIVE_DB --identity PRIVATE_IDENTITY \
  --work PRIVATE_WORK --binary CANDIDATE_BINARY --config PRIVATE_CONFIG \
  --phase inventory --page-size APPROVED_PAGE_SIZE
```

之后使用同一组显式参数依次运行默认 dry-run、`--phase apply`、再次 apply、resume 和 verify。
需实际模拟中断及并发写后恢复，不能以一次空 resume 代替演练。
最终 `--phase verify --write-receipt PRIVATE_RECEIPT` 要求本地历史已完成 dry-run/apply/repeat/
resume/verify、第二遍更新 0、全量 ready。收据绑定包摘要/profile/schema/引擎；检查点与历史只能
留服务器本机。若副本因生产对象并发删除而不一致，取新副本重试，不能删行掩盖。

将来获准 live 阶段还必须显式 `--role live --allow-live --replica-receipt PRIVATE_RECEIPT`，
目标必须与显式 live 身份相同。live 的 `--init-identity` 在验证成功副本收据及只读 schema/引擎
预检后才创建身份。换包/profile/schema/引擎后拒绝旧收据。所有真实值由运维现场提供。

退出码：0 阶段成功；3 身份/权限/参数/收据/输出拒绝；4 元数据或全量约束失败；5 CAS 冲突；
6 预算耗尽；7 内部故障或中断。非零不能当 ready；恢复后须重新 verify。

## 输出与证据边界

stdout 仅一份 JSON，stderr 不输出原始错误。字段限版本/phase/profile/schema 指纹、实际
SQLite 版本/source 指纹、计数、总耗时、HEAD 延时桶及 ready。延时桶为不超过100ms、
超过100ms且不超过1000ms、超过1000ms；状态类别为 stat/head 成功与拒绝、对象缺失、
元数据失败、数据库失败、冲突、预算耗尽。禁止输出任何行、ID列表、路径、key、ETag、日期、
URL、签名、正文、凭据或 SQL 参数。Python 对子进程 stdout/stderr 做私有捕获、大小/白名单
校验，未知输出只返回固定拒绝类别。中断杀子进程组并删除私有请求目录。

只可回传经审核 summary/counts。不要上传 DB、配置、收据、检查点、原始头或进程日志。
本机合成测试 `backfill_acceptance.py` 覆盖默认零写、全量约束、幂等收据、权限/别名提前拒绝、
低 id 重扫、预算恢复、进程中断清理、真实 HEAD 暂停期间的并发 writer/CAS，以及两输出通道哨兵。

`download_preflight.py MANIFEST` 离线校验同一候选 SHA 的 GET/HEAD/PROPFIND 与九类 writer，
要求 metadata_summary 守恒且 ready；只能给 cohort 本地门。服务器相关门独立 PENDING，
production blockers 非空就算出 BLOCKED。manifest 不得含凭据或原始对象标识。

## 服务器证据与 S13 清单

1. 配置所有者在服务器内，从同 O1 引擎备份取得隔离副本；同候选包、独立凭据/socket，
   不写生产 nginx、共享认证或真实对象，不把库拷出服务器。先 inventory/dry-run，再 apply/
   verify/第二遍/中断恢复。确认目录及非表示业务列未变、计数守恒、全量缺值/冲突/错误为零。
2. 另行授权只读源证明：至少30个既有非空对象，不足则全部，按大小/来源/年龄/MIME/存储类别
   分层并执行两轮。同 authority、签名 target、identity、无 fop。分别统计 ETag/LM/完整长度/
   编码/Vary/条件/范围。小对象完整 GET 并在服务器本地校验摘要；大对象按批准预算取完整体
   或 Range 总长度，明确缺少完整大体证据，区间 CL 不算总长。HEAD 全量覆盖率与 HEAD/GET
   样本等价分门。stat 不等只否决 stat 推导；HEAD/GET 不等或变换不稳定则阻塞该 profile。
3. 另获合成命名空间写权限后，验证新建、真实 multipart v2、普通/分片 insertOnly 重放、
   同内容重试 LM、COPY 目标 LM、换 key 覆盖、零字节、UTF-8/百分号 key、跨秒生命周期。
   不允许副本的应用 COPY/PUT 测试修改真实业务对象；fake 通过不能证明供应商永久不变。
4. S13 单独批准备份、additive schema 和限速预回填。停发旧 token，等实际最大 TTL 过期
   并处理旧 pending，或取得等效存储端禁覆盖证据；配置 TTL 上限86400秒，不能默认一小时。
   冻结所有 files writer 和三读方法，排空未提交头的控制请求与在途写，末次全扫和 ready。
   所有 writer 升级为同代包，原子开放 GET/HEAD/PROPFIND 后恢复写入。
5. 显式验证 Finder/Windows WebClient 等实际版本的207、打开、空文件、续传和重开；另测实际
   生产资源限额、CA/socket 所有权、入口配置、匿名对拍与真实用户。现有 S13b-2b 仅授权
   OPTIONS/PROPFIND，K2 不扩权，不把 curl UA 当真实客户端。
6. 回退先恢复本阶段前入口及对应整包，令新请求回旧包，再排空旧 nginx 下载。保留新增列，
   不恢复旧 DB 快照覆盖新写入；旧 writer 再写即使 ready 作废。已发头下载由旧 worker 完成并
   单列；下载无总时限，停止旧 worker 的策略须 S13 明定，不能暗藏超时强杀。
7. 所有保留路径包括写入全量后才开始至少七天观察；K2 不启动时钟、不退休 JVM。真实源、
   全量副本、客户端、生产资源、token 失效和正式切换逐门签收，不以一个 PASS 合并。
