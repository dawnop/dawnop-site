# S10 native 单交换入口

状态：实现完成，验收中。工具链沿用 v0.98.0，不接触语言仓库、服务器、部署包或 systemd。

共享应用与路由迁入 core，业务只保留一份。数据库接缝是纯 Dawn 的连接操作记录与配置中的连接工厂；泛型 with_db 在连接 bracket 内安装原 Db 六臂。JVM 工厂仍为每个工作单元创建 JDBC 连接；native 工厂只在 sqlite.with_db 的动态生命周期内提供当前连接的操作，整个单交换结束后由外层 bracket 关闭。不会把 SQLite 资源带出 bracket，也不新增跨网络等待的写事务。出站 HTTP 记录由两端适配器提供，实际执行路由时安装 Upstream 与 UpstreamBatch。

配置每进程读取一次。新增 --migrate 子命令运行兼容加表，--cleanup-pastes 独立运行清理。native 交换在已有数据库上核对 WAL 与运行表，不执行表初始化；schema 不完整则 stderr 报错并退出。JVM 保持启动迁移以兼容既有启动接口，处理请求不再重复建表。

历史 util/clock.Clock 效果删除，所有 now_ms 调用直接使用标准 Clock 的墙钟毫秒 helper，测试使用标准假钟。服务日志和启动日志统一 stderr，stdout 仅为 HTTP wire。

入口直接使用 web/exchange 的 serve_exchange 与 stdio_transport，不复制解析器。响应完成后，极小 native IO 接缝对 socket 半关闭写端并在时间和字节上限内排空输入，解决早拒绝后未读体引发 RST；普通管道同样有界退出。真实 socket、逐字节 wire、断连、流式大体和取消 guard 验证这一边界。

性能只报告实测，冷启动覆盖进程创建、配置、连接检查、请求处理与退出；monitor 的 CPU 冷窗口 300 ms 明确计入。S8 下载完整暂存带来的 TTFB 限制继续有效，S10 不授权任何路径切流。S11 接管 355 golden 的 native 双臂扩展。

不做的（理由）：不构建常驻 native server，不增加线程或 socket listener，不重录 golden，不修改认证 TTL，不创建部署单元、不部署或合并 PR；这些均属于后续任务或已有裁决禁止事项。

本机已实测完整入口：100 个新进程 health p50 4.127 ms、p95 5.080 ms；monitor CPU 冷 377.711 ms、暖 16.115 ms，含 300 ms 冷窗口。256/512 MiB 全进程树峰值 RSS 上传 18060/17996 KiB、下载 17888/17860 KiB。环境、命令与最终 CI 数字记入 S10 handoff 报告，不将本机页缓存温态当作生产测量。
