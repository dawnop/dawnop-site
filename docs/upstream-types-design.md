# S7 出站类型脱 Java

状态：已实现（2026-10-22）。本刀只改变类型与实现的分层，不改变业务输出。

## 类型与效果

core 的 util/http 持有 OutReq、OutBody、RequestBody、ResponseStream、Pending 与 Upstream 四操作。
RequestBody 不透明包装纯数据：multipart 的 head、文件描述（path、offset、length）与 tail。
file_body 使用标准 Fs 的真实 handler 查询文件长度，错误仍经 fe_text，保持上传失败前缀；不创建 JVM publisher。
当前生产只创建完整文件描述（offset=0），JVM handler 校验描述与实际文件一致后用 ofFile 流式发送。
其他范围返回 ForeignError 并沿原出站错误路径分类为502，不崩溃或静默扩大范围；S8 可在同一描述上实现分块读取，不能静默发送错误范围。
ResponseStream 不透明包装 web8 BodySource；read(pos,n) 仅在泵消费时执行，错误不能变成 EOF。
Pending 继续包装 !io thunk 与 budget_s，保留异步错误解包与统一 timeout 判断。

Upstream 的 exchange / exchange_bytes / exchange_stream / exchange_start 接受 OutReq，分别返回
Result[HttpResp / BytesResp / StreamResp / Pending, ForeignError]。不增加虚假异步实现。

## JVM 边界

app/jvm util/http_jvm 是唯一 java.net.http 与 InputStream owner，四臂继续使用原 send/sendAsync。
HttpCell 只在 main 与路由适配边界传递，移出 Auth；生产仍为一个进程一个客户端与连接池。
request_for 继续统一安装 budget_s，管理 10s、传输 3600s、连接 10s；错误文本与 504/502 分类留在 core。
响应臂立即将 InputStream 经 input_source 转成纯源，再构造 ResponseStream。
关闭、HEAD/无体响应、提前拒绝与断连仍由既有 web 泵 bracket 与 close_stream 持有。

## 验收与负控

保留全部 JVM 测试。response-stream 七项与 http-body 三项逐项映射到新位置/类型，
真实编译后只能打红原语义断言，不删变异；http-timeout 十项只迁移路径。
另证明 core 公共面泄漏 InputStream/BodyPublisher、调用方直接构造 JVM 体、源错误被吞为 EOF 会红。
新增错误源被吞为 EOF 的第八项 response-stream 变异；完整 CI 包含九组 161 变异、native-acceptance、355 golden 与原七 skip、core 零 Java、FFI、fmt。
golden 文件逐字节不变；HTTP 比较仍使用原结构/归一化规则，不扩大为未经归一化全字节承诺。
公共段的 compiler 专属 gate-map/anchor/doc 工具本站不存在，使用本站完整对应门；不进入 dawn-lang。

## 实现与验收形状

core 共36模块、完整四包47源码零Java；HttpCell仅在JVM入口与路由边界出现，Auth不含资源。
22条原HTTP测试全部保留，JVM原444项新增四项站点断言与一项web/source依赖测试，共449项。
native consumer新增四项公开协议测试，可达依赖增加后共86项；S4/S5/S6原负控与sanitizer不减。
response-stream原七项矩阵成员与owner不变，改动只有目标位置与纯BodySource表示；
新增discard-source-error，真实编译后只能打红延迟错误断言。http-body三项owner不变，
leak-java-net-http升级为调用方直接构造BodyPublisher。timeout十项owner不变，JVM锚点迁移；去除分类的变异同步清理不再使用的timeout导入，保证真实编译。
完整CI仍25jobs、19变异分片，不改变workflow或timeout；必须核对161变异完整覆盖。

## 不做的（理由）

- curl、monitor 批量、native HTTP 入口：分别属于 S8/S9/S10。
- 任意 JVM 文件切片的新传输实现：生产没有该调用，本刀保持 ofFile 的恒定内存路径并显式拒绝非完整范围。
- 编译器升钉、golden 重录、CI 拆分、部署或服务器操作：本刀不需要。
