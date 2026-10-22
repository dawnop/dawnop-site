# Native 真单元大体传输的进度门

状态：已实现；以下设计先于动码。本机真单元全套 112.241 秒通过，双臂 355/355
与原 7 skip、四项契约负控均通过；最终 CI 结果见 PR 与交付报告。

主线 739677e1 的 run 39867022562 两次在 512 MiB PUT 的客户端 recv 超时。
下载两次日志与 evidence 后确认：超时来自 harness `connect().settimeout(10)`，
journal 中 PUT 实例是随后被 harness 清理，没有证据表明它触发了 60 秒 socket
空闲或 10800 秒 RuntimeMaxSec。旧 evidence 没有 cgroup/字节采样，不能反推 runner
当时的精确磁盘速率或 memory.events。

本机先用原实现、PR 的已验证 Jammy 包（源代码与合入树相同）复现：保留
MemoryMax=96M、沙箱与父 slice，实例 CPUQuota 收紧至 25%，/tmp 所在块设备读写
各限 32000000 B/s。入站 14.455 秒结束，24.464 秒客户端 recv 超时，此时 multipart
组装仍增长；保持同一 socket，52.982 秒收到 201，假桶的完整摘要为
`c047731a3c134f3d34286d608e9c173027d50f43ab9d2064f3c360939977e908`。
在 16.030/18.067/20.100/21.876/23.905 秒，组装体分别约 0.4/37.9/88.5/151.2/218.3 MB。
最后 memory.events max=9671，oom/oom_kill=0，io.pressure full total=33789595 微秒。
这证明内存约束下页缓存回收及 I/O 背压会延长处理，不能把没有 HTTP 响应字节的
10 秒当作工作停止。它没有证明旧 runner 的每个内部事件都与本机相同。

## 实现与判词

生产的内存、CPU、idle、RuntimeMaxSec 和 curl deadline 保持现值：96 MiB 包含
页缓存，本次在更严格 CPU/I/O 条件下仍有界推进，没有提高生产资源的实测依据。
首版完整磁盘暂存的 TTFB 取舍与 S13 独立门继续有效。

只对离线大体交换使用进度感知客户端。每 250 ms 采集实际实例 cgroup，保存
memory.current/events/stat/pressure、io.stat/pressure/max、cpu.stat/max、各进程
io/wchan、暂存文件尺寸与客户端/假桶字节。进度只认入站已发送字节、multipart
upload 文件增长、假桶实际读取、下载 body 文件增长和客户端实际接收；CPU 时间、
累计读调用、内存回收及压力计数不能延长等待。各计数用高水位避免临时文件删除
造成倒退。连续 10 秒无字节进度即失败，整个交换另限 180 秒，短 recv/send 轮询
只用来检查这两个界限，不把轮询超时当业务结果。失败也落 JSONL 和摘要。

先红负控：在真实 socket 激活的 PUT 实例上 SIGSTOP，让同一进度门看到已发数据
后停止推进，必须因无进度失败；随后恢复并停止本次实例，不留挂起进程。
阳性保留原 shipped 单元的完整 512 MiB 双向摘要、curl loader 隔离，另一次在
相同内存/沙箱和更紧 CPU/I/O 下跑 512 MiB 双向。必须核验实际 cgroup 的 memory.max、
cpu.max、io.max，证明约束真的应用；需要看到超出旧 10 秒的响应等待、实际组装与
接收进度、零 OOM，并限制最大无进度间隔。证据随既有 native-units artifact 上传，
不新增 workflow job，不改变 20 分钟上限。

本机新门的受限上传响应等待 35.151 秒、全程最大无进度 2.506 秒，下载最大
无进度 1.508 秒。受限上传实际读/写约 1.074/1.075 GB，I/O full pressure
累计 35.522 秒；下载实际读/写约 529/539 MB，累计 19.521 秒。两者 oom 与
oom_kill 均为零，完整双向摘要与 JVM 卸包回滚通过。raw JSONL 与命令输出保留
在交付报告引用的 scratchpad，不把一次通过解释为所有生产盘速都可接受。

参考：[systemd v249 资源控制](https://www.freedesktop.org/software/systemd/man/249/systemd.resource-control.html)、
[Linux cgroup v2 的 memory.events、writeback 与 io 控制](https://docs.kernel.org/admin-guide/cgroup-v2.html)。
`max` 是触及上界计数，不能当作 OOM；缓冲写回同时受 memory 与 io 域约束。

## 不做的（理由）

不部署、不碰服务器、不改 nginx 或业务、不放宽生产资源，不把合成盘速当生产吞吐
承诺。不以提高固定 recv 超时掩盖停滞，也不以 CPU 活动冒充有效字节进度。
