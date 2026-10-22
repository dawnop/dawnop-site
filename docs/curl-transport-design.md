# S8 curl 顺序传输臂设计

状态：已按用户2026-10-22追加裁决实现流式上传；本机双臂、全部负控与原内存门通过。基线 origin/main 8b60c70，工具链 v0.98.0。

## 范围与结构

在 app/native 增加 util/http_curl，安装 core Upstream 的四操作。
JVM 的生产入口与既有 handler 不改。每次交换使用独立私有临时目录，
Proc.run 只接 argv，不经 shell。curl 的 -q 必须是首个参数；URL、请求头和
请求选项写入私有配置，默认配置、代理、重定向明确禁用，只允许 HTTP/HTTPS，
TLS 校验开启。错误只报告稳定错误种类/退出码，不回显 URL、凭据或 curl stderr。

管理/文本回复最多 4 MiB；连接 10 秒，OutReq 的管理 10 秒与传输 3600 秒
分别传给 curl。exit 28 构造 TimeoutException 后缀的 ForeignError，交 core
原 deadline_text 与 504 分类；其余 curl/文件错误走 502。非 2xx 仍为收到的
HTTP 响应。失败必须在解析 HTTP 状态前处理，禁止把短体当 200。

上传按照 head、FileSpan(offset,length)、tail，以 64 KiB 范围读有界组装文件，
--upload-file <file>，显式原方法与原始 request-target；二进制不经过字符串。下载完整落盘、确认最终状态和响应头，
再交 response_stream(file_source) 分块回写。source.close 删除所属临时目录；
初始错误、文本/bytes 完成也删除。exchange_start 同步完成交换后返回已结算 Pending，
不声称并发；S9 将引入显式批次。

## 验证与传输门

仓库外 Python 假上游子进程只监听回环。同源 Dawn consumer 分别安装真实 JVM
与 native curl handler；对拍状态、完整 SHA256 与 NUL/multipart/Range，运行
256/512 MiB 上传下载、慢源、早 401、非 2xx、短体、TLS/拒绝连接、磁盘不足、
真实下游断连与临时文件清理。记录每臂首字节/总时间、进程树 RSS/PSS 和磁盘峰值，
明确 JVM 实时流与 curl 全量暂存的语义差异。慢下载和交互预览的暂存 TTFB 若明显
退化，S13 不放行该路径，保留 JVM；不使用 HEAD+GET 冒充单次响应。

原五个真实源码负控先成功编译再要求专属断言红：超时标 502、NUL 截断、丢 Range、
curl 失败伪造 200、漏临时文件清理。新验收接入 native-acceptance；既有 JVM、
355 golden、7 skip、161 变异、core 零 Java 与格式/安全守卫保持。
本站不存在 compiler 专属 gatemap/anchor/doc/steps_lock，使用现有本站对应门与
工作流逐步骤对账；不读写 dawn-lang 仓库，不访问集群或服务器。

## 首轮失败与用户后续裁决

同机真实进程树（扫描每个 IO worker 的 /proc task children）表明，
`--data-binary @file` 的 curl 子进程会物化整个上传体：256/512 MiB 上传的
RSS 峰值分别 533356/1062168 KiB，PSS 526071/1054897 KiB。
Dawn 的 64 KiB 组装循环不能使 curl 的缓冲变为恒定内存。
用户追加裁决明确允许以 `--upload-file` 代替固定选项，原意是有界内存与字节保真。
实现将保留显式 method，并传原始 path/query 作为 request-target，防 curl 在 `/` 结尾
补文件名；用外部假上游的原始请求行逐字节验证。上传体明确 Content-Length 等于组装文件
大小、禁 chunked；空 Expect: 抑制 100-continue，和 JVM 默认臂对拍。原64MiB内存门
及增长阈值逐字节不改，新增实际退回data-binary的256/512MiB负控，必须按原门红。
同时为丢request-target、漏Expect抑制及错误长度增加真实编译负控。

本机慢源 8 MiB（每 64 KiB 停 12.5 ms），handler 内首字节 curl/JVM 为
1.618/0.108 s，总时间 1.659/1.674 s；不包含进程启动/退出，另留原始
进程到 wire 时钟。完整暂存不满足交互预览与 DAV Range 的首字节需要，
FM content、DAV GET/Range 在 S13 继续留 JVM，后续需要专门传输协议刀。
512 MiB 下载 curl RSS/PSS 13980/6507 KiB，磁盘峰值 536870912 B。
这些为首次探针。追加裁决后的GraalVM JDK21本机完整回合164.062s：256/512 MiB流式上传
RSS为14012/14160 KiB，PSS6534/6671 KiB，原64MiB上限及16MiB增长门通过。
退回data-binary的真实编译负控RSS534320/1062264 KiB，在完整摘要和头保真通过后，
由逐字节未改的native-upload-memory断言拒绝。34个单次上传观察同时持有原始
request-target与POST/PUT/Content-Length/无Transfer-Encoding/无Expect合同。
early401假对端先flush响应、半关闭写端，再在2s/8MiB预算内排空，防止未读上传体的立即关闭
把HTTP拒绝变为连接重置；另有peer-close案例严格持有502。两臂401断言保持。
最终CI墙钟将另列。

## 不做的（理由）

不做 monitor 批量、native HTTP 入口或切流，分别属于 S9/S10/S13。
不做 libcurl、自建 TLS、连接池、Proc.spawn，裁决明确禁止。
不部署、不访问真实七牛或外部上游、不合并 PR、不派子代理。
不改变业务输出或重录 golden；性能结论仅来自本任务实测，后续回填。
