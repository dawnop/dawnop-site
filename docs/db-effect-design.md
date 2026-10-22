# Db 效果与 JDBC 适配设计

状态：已实现，完整 JVM 构建与 437 项测试通过，355 golden 与 7 skip 保持；最终 CI 结果见交付报告。工具链保持 v0.97.0，SQLite 引擎与部署接口保持原状。

## 边界

core/db/sql 定义站点 SqlV、Col、Cell、Row、查询结果列名与 Db 效果。操作使用隐含的当前连接，返回 Result[..., String]，不接收泛型回调。query 返回完整列名与已复制单元格，query_rows 在 core 构造名称索引，重复列名后者覆盖。execute 用于 DDL、PRAGMA 与事务，execute_changes 返回 DML 行数，生产 DDL/PRAGMA 调用已分流到 execute，避免读取上一条 DML 的 changes；last_id、busy_timeout、in_transaction 也是明确操作。

事务由普通泛型函数 with_tx 与 with_immediate_tx 组合上述操作，保留即时写锁、嵌套拒绝、Err/panic/提交失败清理。require_tx 读取真实 SQLite 状态。row_sig 仍使用 SQL quote 快照，updated_at 的判定与提交范围不改。

app/jvm/db/jdbc 独占 Connection、语句与结果集，在连接 bracket 内安装 Db handler。ForeignError 只在适配层用既有 catch_text 转换。db/db 的配置记录改名 DbConfig。生产每个原 with_db 调用在回调执行位置安装 handler，不依赖闭包创建时的证据。

将所有 repo 与只依赖 SQL 的 svc/search、svc/export、svc/paste 迁入 core。其余 app 业务也去掉连接参数，显式声明 Db。未来 S4 用 sqlite.with_db 安装相同效果，映射 NULL、列名、事务状态及 last_id；本刀不消费 sqlite 包。

## 验证

保留全部既有测试名称、355 golden 与 7 skip，golden 文件不重录。测试阶段把生产 core 与 app 组合成一次性 JVM 同模块镜像，使 core 白盒测试能使用真实 JDBC；生产依赖图不受影响。保留全部旧变异与专属红集，更新搬迁后的源码锚点。新增缺 handler 编译负控与重复列名后者覆盖负控，嵌套清外层沿用既有真实运行负控。执行 native core 闭包检查、完整构建、格式、FFI、契约与仓库全部 CI。

## 不做的（理由）

不切 SQLite 引擎，不引入 packages/sqlite，不部署或访问服务器，不改缓存或网络接缝，不改 HTTP 契约，不扩展语言效果机制。连接初始化的严格 WAL/扩展能力验证留给 S4 的双臂初始化门，本刀保持原生产初始化行为。
