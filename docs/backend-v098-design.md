# 后端升钉 v0.98.0

状态：版本钉与验收夹具已更新，正式源量墙零诊断。最终测试与 CI 证据见交接目录
`site-b4-bump-v098-report-20261022.md`。

## 目标与步骤

编译器 JVM/native 产物与 web、web_jvm、json、sha2、sqlite 源码包统一消费
v0.98.0 正式发布。先分别核对发布摘要与实际下载字节，再测量归档的 d1 树摘要。
`.dawn-version` 与 `.dawn-version.sha256` 同批更新，native 下载器同步可信摘要。
所有 dawn.toml 的 URL、版本与摘要一起更新；路径依赖保持原物理根。

先在不改 Dawn 源码的情况下运行 app/jvm 的 dawn check，记录真实错误与级联分类。
若有 std/int 具名按位函数、BodySource 回调或直接 read 调用，则迁到运算符与
read(pos, n)，不加旧协议兼容层。测试夹具和变异锚点按真实破坏面同步。

实测 app/jvm 的首次 dawn check 为零错误；本站仅用 input_source，无自定义源或
std/int 具名按位函数调用，所以生产 Dawn 源与原变异矩阵无需修改。S4 验收的
ARCHIVE 摘要也同步新 tag，避免 sanitizer 与包源码负控继续读取旧缓存。
test/sql、test/native 的纯路径依赖自动消费更新后的 core/native，manifest 无需改写。

## 验收

先证明错误编译器摘要与错误源码摘要会被拒绝，再恢复正确钉。
运行 JVM 测试与打包、四套 355 golden 比较和原七 skip、S4 native SQLite、
S5 TTL、S6 认证租约的原验收和先红负控、core 零 Java 闭包与 FFI、fmt、
配置和变异覆盖检查。最终完整 SHA 的 GitHub CI 必须全绿并覆盖原 160 变异。
golden 文件必须逐字节不变，沿用 JSON 结构、XML scrub 与专门 raw socket 比较口径。
锁文件由发布版工具重算并检查，不因源码 tag 变化手改 Maven artifact。

共享任务规则中的 dawn-lang 专属工具不在本站；对应执行本站检查与完整 CI，
不访问另一个写者的仓库或集群。workflow 不改，不拆 job、不改变预算。

## 不做的（理由）

- 不部署、不碰服务器、不合并 PR：本单只交付可审查的版本迁移。
- 不迁移 S7 Upstream：本单仅记录新纯 Dawn 源对后续迁移的帮助。
- 不改变业务、SQL、缓存或认证行为：沿用原契约及负控。
