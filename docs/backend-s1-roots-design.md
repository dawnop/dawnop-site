# S1 后端源码根隔离

状态：已实现。本机 433 项单测、355 golden、固定 7 skip、fmt、FFI 与两项 core 负控通过。完整 CI 和墙钟见交付报告与 PR。

采用三个物理根：`backend-dawn/core/src`、`backend-dawn/app/jvm/src`、
`backend-dawn/test`。core 与 JVM app 各有 dawn.toml，app 通过
`[deps] site_core = "../../core"` 消费 core。core 只声明原有纯 Dawn
web/json/sha2 依赖；sqlite-jdbc 与 web_jvm 只属于 app。

按整个模块的传递依赖分类，不拆现有 Java 实现。全部不含 Java、且闭包不触达 JVM 模块的 19 个源码模块已搬到 core，其余 38 个留 app。后续 S2/S3/S7 才切 Clock、Db、Upstream。

Dawn v0.97.0 没有独立白盒 test 根或 friend 模块机制。现有测试调用模块私有函数，
不能为了搬测试扩大生产 API。测试块与测试专用夹具按原模块路径存入 test，测试脚本
生成一次性 core/app 镜像，把片段接回原模块，再使用原生 Dawn 路径依赖执行全量
`dawn test`。生产 build 直接消费原始 app/core，绝不消费测试镜像。变异 harness
也使用同一镜像，保留真实编译、原矩阵与唯一归属断言。

新增门禁先对 core 的源码与 manifest 依赖闭包扫描，再调用发布版 native dawnc check
检查整个 core 根。拒绝 Java 声明、java-deps、web_jvm，以及 core 回指 app/test。
两项真实负控在一次性副本中分别加入 Java 导入与 app 模块导入，必须非零退出；
不得减少 core 原有模块或测试数量来换绿。

`build.sh [输出名]`、默认 jar/lib 位置、manifest Class-Path、artifact 名与部署脚本
接口全部保持。全量单测、355 golden 与固定 7 skip、fmt、FFI 和九组变异矩阵由本机
定向验证及完整 GitHub CI 验收。

## 不做的（理由）

不迁移 Java 实现，不升钉，不改业务或 golden，不引入 native 服务入口。
不部署、不操作服务器、不合并 PR、不启动子代理，均超出 S1 范围。

测试根保留 299 项本站测试名称与原断言，core 71 项、JVM 228 项；含依赖包共 433 项。
原有 159 个变异体锚点全部可用。46 条 Java 导入分为 app 26 条、test 20 条，core 为零。
core 的 native 检查与完整依赖扫描覆盖 4 个包、29 个源码文件。
