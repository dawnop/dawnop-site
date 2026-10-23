# O1b 备份引擎

状态：已实现。每日备份从系统 SQLite CLI 改用站点钉住的引擎，两臂共用
`!Db` SQL；不改业务，不部署。

## 方案

选择 `VACUUM main INTO ?`。它在读事务里产出包含已提交 WAL 的一致快照，保留源库
内容和日志模式；快照后的提交不进入副本。输出必须不存在或为空。本站持久业务
标识使用显式主键；Paste 是 TEXT 主键并以隐含 rowid 关联 FTS，所以不能泛称
VACUUM 保留所有 rowid。两臂回归通过真实 --migrate 创建生产 Paste schema，
以 rowid=11/55 的空洞数据验证实际固定版本保留 rowid 与 FTS 对应关系；
每份副本的 rank=1 检查也会拒绝未来引擎造成的错配。空闲页与文件布局不是恢复契约。
它会消耗比在线备份 API 更多的 CPU，但无需为 JDBC 和 native 分别添加绑定。没有声称性能改善；实际墙钟在交付报告中记录。

在线备份 API 可增量复制、CPU 较少且保留页布局；本站当前 `!Db` 不暴露该接口，
为一个每日小库任务扩展两个 adapter 和语言包不合算。源库若增大到读事务影响 WAL
回收，应按实测重新评估增量 API，而非自行切回系统 CLI。

官方语义：[VACUUM INTO](https://sqlite.org/lang_vacuum.html)、
[在线备份](https://sqlite.org/backup.html)、
[FTS integrity-check](https://sqlite.org/fts5.html#the_integrity_check_command)。

## 边界

两入口新增 `--backup <dest>`，只开已有源库，不 configure、不 migrate、不请求改 WAL。
专用入口先在内存库拒绝旧引擎，再打开已有源库并复核版本、设置 busy timeout，
严格加载配置的 tokenizer。
core 再经 `!Db` 取快照，attach 副本到同一连接，对副本严格检查
`PRAGMA integrity_check` 的完整结果，逐个 FTS5 表执行 rank=1 的 integrity-check，
包括 external-content 一致性。所有失败传播；关闭连接释放附库。

脚本显式选择 JVM jar 或 native ELF，默认 JVM，保留 gzip、摘要、权限、轮转与 KEEP
判词；无系统 sqlite3 依赖。systemd 仍以 dawnop 跑，应用目录只读，库目录因 WAL/SHM
仍可写，私有 tmp 供 JDBC 解包，定时调整到小时 cleanup 的正常窗口以外。

测试保留既有产物/轮转/坏库/已提交与未提交判词，两臂运行。额外覆盖 CLI 参数、
源库未迁移、FTS 损坏、版本门、写入时序，以及能编译执行后令专属判词红的真实变异。
完整 CI 保持原 161 变异与双臂 355/355、7 skip 不变。

## 不做的（理由）

- 生产安装与任何服务器操作：没有授权，此刀只提供协调者可执行的步骤。
- 在线备份 API 绑定：现有 SQL 能满足任务，避免触及另一个写者的语言仓。
- 异地备份与密钥备份：独立运维需求，当前仍仅防同盘误改误删。
- 修改业务或契约 golden：维护命令不改变 HTTP 契约。
