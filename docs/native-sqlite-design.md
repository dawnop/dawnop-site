# Native SQLite handler（S4）

状态：已实现。验收入口为 `backend-dawn/scripts/native-sqlite/run.sh`；本机性能采样加
`--benchmark 300`，原始输出通过 `--work <仓库外空目录>` 保留。

实现位于 `backend-dawn/app/native`，与 JVM 根并列。只依赖 core 与通过 v0.97.0
归档、d1 hash、subdir 固定的 sqlite 0.1.0；core 不回指 adapter。

`with_db(path, ext, body)` 返回 `Result[T, String]`，在 sqlite.with_db 的连接 bracket
里先设置 5000 ms busy_timeout，实际查询 `PRAGMA journal_mode=WAL` 并要求返回 wal，
再按需通过 C API 和固定 `sqlite3_simple_init` 加载扩展。初始化失败不执行 body；
正常关闭失败同样返回错误。空 ext 表示该工作单元不需要 FTS。SQL 扩展入口始终关闭。

六个 Db 操作直接映射 sqlite 的物化查询、execute、execute_changes、last_id、
busy_timeout、in_transaction。NULL 先于列类型，Bool 保持 0/1，Rows.names 原样传给
core；重复 label 的后者覆盖、SQL quote row_sig 与事务组合继续由 core 负责。
不修改 JVM 连接初始化、SQL、生产入口、部署或缓存。

验收使用同一个固定 schema/数据快照的两个副本，JDBC/native 执行同一份 Dawn 场景，
逐项比较物化值、labels、DML 计数、DDL 后读值和事务结果，并检查明确的期望值。
真实 simple v0.7.1 独立覆盖中文、拼音、排序、分页、published 与三个触发器；
夹具下载或摘要失败即失败。加入跨进程 BUSY 窗口与完整插桩 SQLite 的 ASan/LSan。
真实源码负控先红，原实现随后通过。CI 打印各阶段及总墙钟。

SQL 结果比较含正常值、列名、DML 计数和事务后的数据。驱动诊断保留各自的
ForeignError 文本；错误路径核对共同 SQLite 原因和 Err 分支，不把两个驱动的诊断前缀
冒充逐字节相同。FTS 的 UPDATE 同时验证旧词消失和新词可检索。

## 不做的

- 不迁缓存、认证与 native HTTP 入口；它们分别由 S5/S6/S10 交付。
- 不升级 SQLite 引擎或改 JVM adapter；引擎补丁来源仍由 O1 单独验收。
- 不部署或改服务器；本刀只交付可独立消费、在 CI 中运行的 native 数据库边界。
- 不用 LIKE 通过真实 FTS 验收，也不重录既有 HTTP golden；原 355 条仍保持其原有口径。
