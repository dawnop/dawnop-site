# SQLite 五槽缓存

状态：已实现，验收结果见任务报告；工具链保持 v0.97.0。

缓存使用业务数据库的独立 `runtime_cache` 表，主键 `(namespace, cache_key)`，值为 `payload` 与 `created_ms`。五个版本化命名空间分别用于 monitor、respack、space、dav 与 cpu；每槽固定一个键，保持原来的单格覆盖语义。TTL 由读取方给定，CPU 的可调年龄上限因此仍有效。时间以 std Clock 的 wall ns 换算为毫秒，持久化值可跨进程使用。有效区间为 `created_ms <= now_ms < created_ms + ttl_s * 1000`；时间倒退拒绝命中。

core 的存取只依赖 `Db` 与既有 std `Clock`。读取只执行 SELECT，不建表、删除、刷新时间或写命中次数；仅缺失 runtime_cache 表视为 miss（恢复旧备份或测试替换数据库）；其他数据库错误向调用边界返回失败。建表在启动时执行，并在写入短事务内幂等确保，以兼容测试夹具或数据库替换后的首次写入。写事务只包含 DDL 与单行 UPSERT，不包网络、bcrypt、CPU sleep 或 /proc 采样。JVM 薄包装持有数据库路径和槽地址，每次工作单元安装 JDBC 和真实 Clock；native 测试安装 S4 adapter 和同一 core。

monitor 保留 120 秒和 refresh 绕过；respack 保留 300 秒、monitor/FM 共享、只缓存成功；space 保留 600 秒和 null 结果；dav 保留 300 秒、单个成功凭据摘要覆盖；CPU 保留 300 秒年龄上限、先读后采、每次调用回写、无效差值与陈旧样本退回 300ms 窗口。CPU 持久化额外带 /proc boot_id，重启身份不符或无法取得时拒绝复用，计数器回退继续由原判据拒绝。

同一 test/sql 场景在 JVM/JDBC 与 native/SQLite 执行，假钟覆盖到期前 1ms、恰好到期、倒退、命中后不续期，真实新进程覆盖两臂互读。只读连接及外部持写锁测试使任何命中写库可观测。五个独立源码负控必须成功编译后按对应断言失败。原 437 JVM 单测、355 golden、7 skip、160 变异及 S4 acceptance 均保留。

## 不做的（理由）

S6 的认证键 HMAC、账号版本联查与短租约防击穿留在独立刀，本刀保留现有验证策略；不增加认证成功的寿命。固定五行 UPSERT 不需要过期清理任务。不改 HTTP 输出、生产部署、native HTTP 入口、SQLite 引擎版本或出站实现。
