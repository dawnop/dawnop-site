# 后端升钉 v0.97.0

状态：迁移已实现，本地验证通过，PR 全套 CI 结果记在交接报告。

## 问题与方案

当前编译器与源码包钉在 v0.94.0。v0.97.0 的 web 7.2.0 已把 JVM
服务器拆到 web_jvm 1.1.0，流式响应接收纯 Dawn 的 BodySource。
本次保持 JVM 后端与既有业务行为，编译器版本和校验和同批更新，
web、web_jvm、json、sha2 使用同一 tag 归档和树哈希。

先用发布版 dawn check 记录诊断原文并按根因与级联分类，再改导入与
流适配调用。服务器改用 web_jvm/server，表单解析用 web/wire，
DEFAULT_MAX_BODY 用 web/types，InputStream 在现有 HTTP owner 内经
input_source 包装。不加兼容别名。

## 落点与验证

更新版本钉、包依赖、入口、鉴权及 HTTP owner；同步 FFI 检查器与流边界
变异体的真实源码锚点，保持七个负控各自唯一归属。运行发布工具链的
build.sh、单测、fmt 检查、四套 golden、settings 与 paste 黑盒检查，
最终由 PR 全套 CI 验收。golden 文件应无变更，比较粒度以现有契约实现为准。
具体诊断、命令输出、计数与结果记在交接报告。

## 实测结果

正确依赖钉下初墙为 9 条诊断：2 条模块搬迁、1 条 BodySource 参数不匹配、
6 条导入错误级联。只改入口、鉴权与 HTTP owner 三个源码文件后，check 为 0。
GraalVM CE 21.0.2 下 build.sh 的 433 项单测通过，四套 golden 比较
58/102/104/91 项全部通过，7 条具名 skip 保持原样；golden 文件逐字节未改。
现有契约对 JSON 做结构比较、XML 做既有 scrub 后的文本比较，另有专门的
裸 socket 响应字节案例，故此处不把全部 355 条声称为原始 HTTP 字节比较。
settings、Paste 黑盒检查与 fmt 检查通过，7 个流边界变异体全部可编译且各自
唯一打红指定断言。Java 依赖 lock 重检无变更。

## 不做的（理由）

不迁 native，不替换其余 Java 调用或 JDBC，不改数据库、部署配置与
服务器；这些都有独立迁移边界，本次仅记录后续可用原语。
不重录 golden 来吸收差异；若现有契约变化，先查明原因。
