# 后端升钉 v0.104.0

状态：迁移完成，本机验收通过；完整 CI 由 PR 验收。

## 范围与依据

从 origin/main 的 v0.100.0 升到 v0.104.0。编译器 JVM/native 校验和与所有 tag 归档依赖同批更新，重新计算归档 d1，复算 JDBC 锁。web 从 8.0.0 升为 9.0.0，保留依赖别名 web。

已阅读 v0.101、v0.102、v0.103、v0.104 正式发布说明。中间版本的 GPU、tileir、xmap 破坏性变化不在本站依赖闭包内。v0.103 排除依赖测试体的变化需以完整 JVM 测试和 native 验收确认。web9 为拒绝型响应构造/修改函数增加尾部 `at: Loc = caller()`；直接调用可省略，函数值须适配 Loc，转发包装须传递调用方位置。先用新 pin 执行 dawn check 量墙，再审阅生产源码、测试及宿主脚本内嵌代码，逐处迁移，不加兼容层。

## 验收

本机执行 JVM 测试与构建、core 零 Java 及真实负控、FFI、fmt、原 golden 和全部变异锚点预检。完整 GitHub CI 验收九族变异、S4/S5/S6 native acceptance、S11 JVM/native 双臂及现有其它门。golden 文件字节与七个 skip 保持基线，不重录。报告记录检查命令、迁移点、原始输出、CI 完整 SHA 与墙钟。

## 实际迁移

初次量墙 `dawn check backend-dawn/app/jvm` 为 ok。直接调用可以沿用默认位置，因此编译成功并不能证明包装已经正确转发。

- `util/http.stream_response` 增加 caller 默认的 Loc，转给 streaming。
- `api/webdav.add_headers` 增加 Loc，fold 闭包内显式传给 with_header。
- `util/local_empty.respond` 增加 Loc，转给 raw 以及每层 with_header。
- `api/api_fm.util_empty_response` 增加 Loc，继续转给 local_empty.respond。
- `response-stream-boundary-mutants/mutate.py` 的 break-owner-adapter 与 bypass-streaming-symbol 两处函数值都显式传递 length 和 Loc；前者的锚点同步新包装声明。FFI 检查器同步声明与转发锚点，保留原 owner 和判词。

其余构造调用位于业务响应创建点，沿用直接调用默认位置；固定状态的 receipt、options 与已验证的 handoff 在自身业务构造位置报告。生产源码与测试不存在受影响构造器的函数值，宿主脚本内嵌的函数值仅上述两处。tokenizer 自测的残片只测试词法识别，不作为可编译 consumer。

三条回归分别验证 stream、DAV 头和空文件响应拒绝的文件/行位置；stream 还验证显式 Loc 精确转发。独立一次性镜像分别删除实际转发，每个负控必须先编译成功，再仅在指定新增断言上失败。未改变原先的流/FFI 变异矩阵和断言。

本机 JVM 469 项通过，契约 366 matched、原七 skip，core 闭包 81 源零 Java，fmt、静态门与全部 161 锚点通过。JDBC 锁复算无 diff。完整 CI 的 S4/S5/S6、S11、九族变异及包验收结果记录在外部交接报告。

## 不做的（理由）

不改业务行为，不放宽门禁或修改 workflow，不增加兼容层。不合并、不部署、不访问服务器，不做 S13b 切流；本任务只交付一个待审 PR。
