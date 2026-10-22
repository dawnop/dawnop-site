# CI native 验收拆分

状态：已实现。冷缓存完整 CI 39779683876 全绿；草稿 PR 的故意失败轮次
39781521530 已证明 native、汇总与 workflow 同时红，随后撤掉探针。
最终 head 的热缓存完整验收与逐 job 墙钟见 agent-handoff 的
site-ci-split-report-20261022.md。

## 目标与依据

把 S4 SQLite、S5 TTL、S6 WebDAV 认证验收原样移到一个独立的
`native-acceptance` job，与 JVM `backend` 并行。主干 v0.98.0 前序 PR
CI 39775800486 的 backend 为 439 秒，其中 S4/S5/S6 分别为 125/49/177 秒。
本次另采 origin/main 完整 CI 39778815832 作为改前对照。

选择一个 native job：三步共用 JDK、两个 Dawn 编译器、coursier 和下载夹具；
只有 S4 编译全插桩 SQLite 引擎。现有 19 个变异分片已经超过账号的 20 并发上限，
继续拆三刀会多抢两个 runner 槽且重复准备，难以缩短由变异分片主导的整条流水线。
单 job 保持三步顺序及 20 分钟上限，缓存减少冷引擎的重复编译成本。
实际墙钟受 runner 和排队影响，最终数据在 agent-handoff 报告，不宣称隔离性能提升。

## 缓存

下载缓存只存既有 fixtures.lock.json 固定摘要的 zip；每次消费仍验 zip、SQLite
源码与 libsimple 共享库摘要。actions/cache 精确键含锁文件摘要、系统和架构，不回退。

san 引擎缓存键包含 SQLite 源码摘要、编译与 sanitizer 参数、编译器版本、平台/libc
及缓存实现摘要。只缓存编译好的引擎和其内容摘要，不缓存任何测试、负控结果、数据库、
消费者二进制或 loader 对象。命中时重新验源码与引擎摘要；缺失时完整编译后原子写入。
每一轮仍真实运行全部 ASan/UBSan/LSan 正控与漏 finalize 泄漏负控。

## 汇总与验收

`mutants-complete` 增加对 native job 的依赖，保留 always() 和全部覆盖检查。
最后显式断言 native job 的结果必须 success；其他检查即使失败也不隐藏这个断言。
在草稿 PR 用单独的故意失败提交证明 native 与汇总、整个 workflow 都红，随后撤销。
冷、热缓存各跑一次完整 CI，记录全部 job 执行时间、排队及端到端时间。

## 不做的（理由）

不改生产代码、测试内容、负控、变异分片或 timeout；本刀只调整 CI 编排与可复用构建输入。
不操作 dawn-lang、集群或服务器；本站不存在的 compiler 专用 steps_lock/gatemap/anchor/doc
工具不从兄弟仓调用，以本站完整 CI 和原检查集合对账替代。
