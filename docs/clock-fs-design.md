# Clock 与 Fs 标准原语迁移

状态：已实现；JVM/native 验收、负控与 CI 实测见 agent-handoff 的 S2 报告。

S2 以 v0.98.0 的 std/io 为唯一宿主时钟与文件大小接口。墙钟用于持久化 TTL 与日期，纳秒除以 1000000 得毫秒；进程内耗时使用 Instant 与 elapsed_ns。JVM 异步批次仍在每项完成回调采结束时间，不能让 join 顺序改变单项耗时。

CPU 保留 S9 的 CpuWait 采样接缝与原四项变异归属，真实等待调用 sleep_ns。测试假钟以独立 wall/mono 状态记录每次 sleep，推进两个时基而不真实等待，允许单独回拨墙钟。CPU 与 TTL 测试共用此模型，不以调度时间判断是否开窗口。

Clock/Fs 接缝位于 core；同时将依赖闭包已可移植的 JWT 与七牛 rs 模块迁入 core，JVM 专用白盒夹具继续留在 test/jvm。

上传临时文件使用 file_size 的 Result。目录、缺失和访问失败保持失败，WebDAV 映射 500，drop 映射 Failed 后沿用现有 HTTP 分类；真实空文件可以成功返回 0。disk_space 使用 available 对齐原 usableSpace，失败沿用监控容错的零磁盘字段。

先在隔离副本证明纳秒换算差千倍、墙钟用于耗时、目录大小当成功三个负控会红，再运行正控。原 355 golden 文件和七 skip 不改，原 161 矩阵保持真实编译与专属断言。新负控独立验收，不改变原矩阵数量。

## 不做的（理由）

不改 native 入口、早 401 波动、传输策略或业务输出；这些属于 S10、issue 23 及既有 S8 切流边界。不部署，不访问服务器，不修改 dawn-lang。
