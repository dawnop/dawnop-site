# Monitor 显式出站批次

状态：已实现并通过双臂验收，工具链 v0.98.0。

core 增加相邻效果 `UpstreamBatch`。输入是含稳定 ID 的 OutReq 列表；输出逐项携带
ID、curl 风格退出码、HTTP 状态、响应头、正文、单项单调耗时与 ForeignError。
错误分类继续由原 http deadline_text 决定。重复 ID 拒绝，缺失结果报错；禁止按完成位置寻址。

monitor 第一轮列出 Tencent 三项、Kodo 四项、CDN domains、按缓存需要的 billing 两项及
vault alive/config。第二轮只包含依赖 domains 的 flux/bandwidth。core 负责按 ID 解码和隔离；
120s 聚合缓存和300s billing缓存仍由入口传入，quota 和本机 /proc 仍在 JVM 边界。

native 每轮只执行一次 curl --parallel，复用 S8 的私有目录、配置引用、argv 策略、上传
装配、大小限制及错误脱敏。每项 write-out 输出带内部数值 ID 的独立行，读取 exitcode、
http_code、time_total；总退出码不能决定其他项结果。JVM 使用现有 HttpClient，保持同一协议。
并行墙钟的断言和负控来自独立回环延时服务。本机六项各900ms，native/JVM批次
实测931/992ms；强制串行两臂均被原2400ms门拒绝。
正式脚本先编译并杀死九项源码负控，再跑相同consumer两臂；本机全回合114.980s。
每项完成耗时按稳定ID关联，另查实际peer完成事件确为乱序，shim断言每轮恰好一次curl。

CPU 测试把窗口等待抽成可注入操作，并给 /proc 固定计数器；数等待次数而不计墙钟。
已有 always-sample-a-window 变异必须仍只击中原 owner，不重试、不改300ms窗口。

## 不做的（理由）

不改 HTTP 输出、单交换入口、进程并发原语、部署或下载流式策略；分别留给既定后续刀。
S8 Pending 兼容接口保留，monitor 生产路径不以 Pending 表示批次。
CPU 冷样本300ms不能与阻塞 Proc.run 重叠，冷访问预算须直接加上该窗口。
