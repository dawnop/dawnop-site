# S11 黑盒双臂与测试夹具

状态：实现完成，门禁证据见 S11 交接报告。基线 afdb569，工具链 v0.98.0。

四套既有合同分别对 JVM 与 native 执行，每臂独立建立 fixture、假桶和配置。
native 测试监听器只负责 accept，将真实 accepted socket 直接作为单交换 ELF 的
stdin/stdout；HTTP 解析、guard、响应及 teardown 都由生产入口负责。每次请求新进程，
迁移在套件 reset 之后显式运行。相同脚本、golden、归一化与七个具名 skip 原样复用。

每套收集既有 Golden 的机器报告，核对完整 case/skip 集合。native 执行用 strace
追踪 execve，并以无 Java 的私有 PATH 执行；包括 migrate 与所有后代，任何 Java/JAR
执行尝试失败。负控必须实际证明响应变化、漏 case、新 skip 和偷偷启动 JVM 会红。
另以固定摘要 simple 夹具验证真实搜索路由，结果不计入 355。

旧测试中的 Java 能力按原测试名字映射到 handler 或外部回环服务。保留适配器自身
必须使用 JVM 的测试与变异归属，不能以搬出源码图替代覆盖。迁移表在交接报告登记。

CI 将新验收放在较短的 backend job，避免向已有 native-acceptance 的 20 分钟预算
叠加负载；记录实际墙钟。所有原 161 变异、native 验收、格式与 JVM 单测继续运行。

## 不做的（理由）

不改 golden、归一化或 skip，不改部署包/systemd，不切流、不部署、不访问真实服务。
S8 下载暂存的首字节限制与 O1 引擎核验仍由原任务负责。

## 实测发现与实现

首次 native 完整跑 351/355：Qiniu 的 HTTP 612 被 curl owner 错拒成 502；
两个裸 HTTP case 还观察到 207 reason、Date、头顺序及 Connection 差别。
修复在产品边界：curl 接受 100..999 的真实三位状态，仍拒绝缺状态/头不一致；
wire/headers 在 web/exchange 第一次完整 head write 上保持既有应用 metadata，
207 空 reason、Date、固定头顺序与 JVM 的原合同相同，移除 Connection。
first-write 是单交换进程内的 C 标记，后续 body 原始 Bytes 不解码不重写；
框架仍负责全部 HTTP parser、framing、guard 与 stream 生命周期。
没有改四套 normalizer 或 golden。S10 raw 探针单独核对唯一当前合法 Date，
其余 wire 字节仍精确比较；新增 Date 不是四套 golden 的新归一化规则。

本机首次修复后两臂均 355/355、原七 skip，simple 0.7.1 的真实 HTTP 中文/拼音、
排序、草稿过滤与分页另验一致。四个负控先实际跑红，再接受正常双臂。

#23 本刀保留 OPEN。原假对端先 FIN 后按 2s/8MiB 双界排空，JDK 有时直到关闭
才结算早拒绝；既有 barrier 尝试产生循环等待。没有取得能满足“客户端完整读到响应
后才关闭”或“JVM 路径不依赖 peer close”的确定性修复证据，因此不放宽、不重试、
不把单次通过解释为根治，后续仍须解决。S8 原门与时限全部保留。
