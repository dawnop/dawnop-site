# Native 候选包与离线验收

本目录交付 S12 的 0% 候选安装。生产仍由 Dawn JVM 承载；运行安装命令需要用户另外批准。
不修改 nginx，不开启写镜像，不替换 JVM 的 jar/lib 或 `.prev`。

## 产物与身份

CI `native-package` 固定 Ubuntu 22.04，20 分钟上限。`backend-native-<完整 SHA>` 包含
`dawnop-native`、`lib/libsqlite3.so.0`、`extensions/libsimple.so`、六份单元/配置、
安装脚本与自包含校验器。manifest 记录每项 SHA-256/大小、源码 ZIP/源文件 SHA、
SQLite source id、编译参数、平台、编译器和各 ELF 实际要求的 glibc 版本（不得超过 2.35）。
SQLite 3.54.1 使用 `-O2 -shared -fPIC -DSQLITE_ENABLE_FTS5 -DSQLITE_THREADSAFE=1
-Wl,-soname,libsqlite3.so.0`，链接 `-lm -ldl -pthread`。simple 0.7.1 ZIP 与 .so 均固定摘要。

打包构建在独立应用副本中加入 constructor，不改业务源码或语言包。
constructor 以 `/proc/self/exe` 找到 release，相邻私有库必须是普通路径；绝对路径 dlopen，
再核对 dladdr、SONAME 重开和 inode。实际版本低于 3.51.3、非固定 3.54.1/source id、
FTS5/线程/扩展功能不符或 simple 无法加载均拒绝启动。
`dawnop-native --engine-info` 仅用内存库向 stderr 输出实际路径与 SQL 版本/source id；
stdout 为空。不设服务或全局 LD_LIBRARY_PATH，子进程不继承私有搜索路径。

## 单元预算

| 项 | 参数与理由 |
|---|---|
| 激活 | Accept=yes，stdin/stdout=socket，stderr=journal；一交换后结束 |
| 权限 | 0660，沿用应用角色与 nginx 组；tmpfiles 建共享父目录，实例退出不删父目录 |
| 突发 | MaxConnections=64、Backlog=256、TriggerLimitIntervalSec=2s、Burst=512；保留 40/s 突发余量 |
| 生命周期 | Restart=no、[Unit] CollectMode=inactive-or-failed、KillMode=control-group；单次失败由下一连接恢复 |
| 时限 | RuntimeMaxSec=10800；为入站、curl、回写三个顺序阶段留预算；socket 收发空闲各 60s；curl connect/管理 10s、传输总 3600s；stop 5s |
| 内存 | 单实例 MemoryHigh=192M 开始回收，MemoryMax=256M 作最后硬保护；包含后代和页缓存，总 slice 仍 2G，四个大体传输硬预算 1G，给轻请求与主机 JVM 留余量 |
| 任务/CPU | 单实例 16 tasks、100%；总 slice 1024 tasks、200%，限制峰值争抢 |
| 沙箱 | NoNewPrivileges、ProtectSystem=strict、ProtectHome、PrivateTmp；ReadWritePaths 指向 DB 整个目录 |
| cleanup | 同 ELF --cleanup-pastes，TimeoutStartSec=300；与 JVM cleanup service/timer 冲突 |

10800s 是进程总硬截止；socket 60s 是单次收发的空闲超时，不能冒称逐阶段总截止。
入站/回写仍共享总截止，curl 自己有 3600s 总截止。S13 nginx 必须有同意的客户端/上游
超时与大体传输并发门。64 个名额只验轻量连接；大体传输首版至多 4 并发，暂存盘至少
8 GiB 可用、运行中保留 3 GiB。未验生产限制和 S8 暂存 TTFB 门前，传输路径继续走 JVM。

这些 socket、资源和时限指令均在目标 systemd 249 手册中；CollectMode 属于 Unit 节。
超过 MaxConnections 会拒绝连接，超过 trigger 限制会使 socket failed。
参考 [v249 socket](https://github.com/systemd/systemd/blob/v249/man/systemd.socket.xml)、
[v249 service](https://github.com/systemd/systemd/blob/v249/man/systemd.service.xml)、
[v249 resource control](https://github.com/systemd/systemd/blob/v249/man/systemd.resource-control.xml)。

## 安装与撤回

只接受显式 40 位 main SHA。安装器要求同 SHA 的整个 main/push CI completed/success，
所有 26 个 job（含 19 变异分片）全部成功；仍在跑、跳过或失败均拒绝。
独立核对 GitHub ZIP digest，再核对 manifest 完整文件集合和各项摘要。
artifact 上传成功本身不构成部署许可。PR merge SHA 的包仅供验收，部署取合入后 main SHA。

获批后的调用方式（不要加代理环境前缀）：

```sh
ssh <user>@<server> 'sudo bash -s' < backend-dawn/deploy/deploy-native.sh <full-sha>
```

安装在 `/opt/dawnop-dawn/native/releases/<sha>`，root 只读；`current` 原子链接切换，
`.prev` 保留上一 native release，和 JVM 的 `.prev` 完全独立。换包前停止 socket 接收，
再确认无实例；已有连接时拒绝换包，不杀请求。失败恢复原链接和单元文件。
安装器执行 systemd-analyze verify、tmpfiles、daemon-reload，仅启用候选 socket；
cleanup 默认仍归 JVM。没有业务探针、迁移、流量修改或 JVM 重启。
撤回命令由安装器打印，调用已安装的 Python helper，无需网络。第一次撤回只停用
native socket/timer；后续可恢复经过校验的 `.prev`，保留数据库和 WAL/SHM。

S13 若批准更换 cleanup 执行者，必须先停止并禁用旧 timer，再启用新 timer；核对最终
只有一个 enabled/active。Conflicts 是运行时互斥，不代表两个 timer 可以同时 enable。

## 复现与切流前置

```sh
python3 backend-dawn/scripts/native-package/package.py build <release> --work <new-work>
sudo env "PATH=$PATH" "JAVA_HOME=$JAVA_HOME" python3 backend-dawn/scripts/native-package/offline.py --release <release> --work <new-evidence>
python3 backend-dawn/scripts/native-package/stress.py --release <release> --source <new-work> --work <new-stress-evidence>
```

真单元验收只写本机唯一前缀的 `/run` 单元、隔离 DB/配置和假上游，结束清理。
先证明六项负控会红，再验 64 真实例、私有 maps、512 MiB 双向全摘要、curl 隔离、
空闲和 RuntimeMaxSec、kill 后恢复、cleanup 与同库 JVM 撤回。压力测试仅证明运行观察：
**未复现不证明补丁存在，版本 provenance 是硬条件。**

大体客户端使用字节进度门：每 250 ms 保存暂存文件、客户端/假桶字节与 cgroup
memory.events、I/O 压力和实际资源参数，连续 10s 无进度或单交换超过 180s 则红。
磁盘组装期间没有 HTTP 响应字节不等于停滞。SIGSTOP 真 PUT 实例的负控必须红，
然后在原单元及 CPUQuota=25%、/tmp 设备读写各 32 MB/s 的更紧条件下各跑一次
512 MiB 双向摘要；保留 shipped 的 192/256 MiB 软硬限制，并核验约束实际生效、零 OOM 和最大
无进度间隔。`*-upload.jsonl`、`*-download.jsonl` 及对应 JSON 随 native-units
evidence 保留，失败也落盘。根因与原超时复现见
[进度门设计](../../docs/native-package-upload-design.md)。

协调者在 S13 切流前另做生产只读核对：所有 RW/checkpoint 客户端、系统包完整版本、
JRE 与 JDBC maps、保留的修复 JVM 整包、候选/扩展摘要与依赖、实际服务用户/组、DB
目录/文件身份与权限、暂存盘、当前单元及 nginx 实际入口；还要确认 cgroup v2 与
memory.high/memory.max 实际为 192/256 MiB（MemoryHigh 仅在 v2 生效）。用同一 release 的内存探针
确认私有路径/source id，不能用系统 sqlite3 或 ldd 替代。业务库/schema/FTS/备份恢复
需要单独批准的受控验收；旧系统 SQLite 不得写或 checkpoint 共享库。

nginx 配置由运维所有者准备：默认 JVM、稳定分组、每请求只选一个上游、HTTP/1.1、
Connection: close、原 URI/头/Range/DAV 转发、大体并发与磁盘门、proxy_request_buffering off、
禁止跨臂重放变更请求。必须 nginx -t，演练失败不切与回到 JVM 后再排空 native。
S12 没有生成或 reload nginx；S8 TTFB 限制、issue #23 和全量后七天观察仍有效。
