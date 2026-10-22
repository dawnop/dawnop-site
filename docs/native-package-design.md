# Native 候选部署包

状态：S12 已实现并通过本机真单元验收；完整 CI 为交付门，不授权部署或切流。工具链固定 v0.98.0。

CI 在 Ubuntu 22.04 构建 `dawnop-native`、SQLite 3.54.1 私有动态库与
simple 0.7.1。manifest 同时锁源码、资产与每个文件摘要、编译参数、source id、
ELF 平台及所需 glibc 版本。部署必须指定完整 SHA，并验证同 SHA 的整个 CI
及所有 job 成功、GitHub artifact ZIP digest、manifest 完整文件集合与逐项摘要。

打包构建在一次性应用副本加入 C constructor。它在 Dawn 入口前，按
`/proc/self/exe` 找到相邻 `lib/libsqlite3.so.0`，用绝对路径 dlopen，再核对
dladdr 与 basename dlopen 指向同一 inode；不设置 LD_LIBRARY_PATH，清除继承的
loader 环境。缺库、系统回落、版本低于 3.51.3、source id 不符或 FTS/扩展失败
均在 HTTP 输出前终止。`--engine-info` 只开内存库，报告实际路径、SQL 查询的
版本/source id 和编译选项。真实请求另以 maps/strace 验证实际包使用同一库，
curl 子进程另核验没有私有 SQLite 映射或 loader 搜索变量。

socket 使用 Accept=yes、0660、64 个实例名额、256 backlog、2 秒 512 次触发。
systemd v249 官方手册已有这些指令；超限会拒绝连接，触发限额会使 socket failed，
不能把它们当业务排队。服务 stdout/stdin 都是 socket，stderr 是 journal，
Restart=no、CollectMode=inactive-or-failed、KillMode=control-group；保留 JVM
五项沙箱，DB 整个目录可写。每实例内存 96 MiB、16 tasks、100% CPU；slice
总内存 2 GiB、1024 tasks、200% CPU，给 4 GiB 主机上的 JVM 保留余量。
RuntimeMaxSec=10800 为入站、curl、回写三个顺序 3600 秒阶段留总截止；socket
空闲收发超时各 60 秒，curl 原有 connect 10 秒、管理 10 秒、传输总 3600 秒。
cleanup oneshot 用 TimeoutStartSec=300（RuntimeMaxSec 对 oneshot 不生效），
与 JVM cleanup service/timer 冲突，候选安装默认保留 JVM timer。

安装使用不可变 release 目录与 current 符号链接原子替换，`.prev` 保留上一链接。
安装及 undo 均不改 nginx，也不停止 JVM；只有 socket 启用。无现有实例时才能换包，
避免一个请求中从不同 release 打开文件。部署预检要求暂存盘至少 8 GiB 可用，
S13 必须在真实 nginx 对大体传输限制至多 4 并发并保留至少 3 GiB 磁盘，未完成
这些准备以及 S8 TTFB 门以前，传输路径留在 JVM。64 是轻量连接验收，不是允许
64 份 512 MiB 同时暂存的容量承诺。

离线真单元以唯一测试前缀写入本机 `/run/systemd/system`，隔离 DB、配置、假桶与
包；保留生产沙箱指令，仅替换测试路径和本地服务身份，结束删除本次测试文件。
先做六个要求的负控，再测 64 活跃连接、512 MiB 完整摘要、空闲/总截止、kill
恢复、WAL 目录权限、cleanup 互斥及 native 卸下后 JVM 继续读取同库。
大体另有真实停滞负控、字节进度 watchdog 和 CPU/I/O 收紧回归；页缓存回收不再
误判为客户端固定 10 秒响应超时，详见 [进度门设计](native-package-upload-design.md)。
O1 压力独立混合 JDBC 3.53.4 与 native 3.54.1，记录短写、读、完整重复
checkpoint、WAL reset、关闭重开与 BUSY。未复现不证明补丁存在，版本 provenance 是硬条件。

## 不做的（理由）

不改业务、nginx 或生产状态；S13 才负责切流。不给旧系统 SQLite 写/检查点权限，
不以一次压力测试替代来源证明。不删除 JVM 热备或恢复数据库旧快照。
