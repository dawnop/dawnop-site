# K2 本地验收

所有数据均为合成 SQLite 与回环 TLS fake；nginx 使用独立 prefix/config/socket/temp。
编译器仍为钉住的 v0.100.0。未访问服务器、真实桶、私有 ops 或生产数据。

| 门 | 本地结果与边界 |
| --- | --- |
| JVM build/单测/初始化 | 466 单测、构建、JDBC artifact 与旧引擎/初始化负控通过 |
| native SQL | 原30观察与 JDBC 一致，FTS6一致；BUSY、事务、四种跨引擎 WAL 回退、ASan/UBSan/LSan及泄漏负控通过 |
| 四 golden | JVM/native 各366：58/102/104/102；原七skip，无新增/缺失/未审差异；read/edge零diff |
| P3 | 两臂各0/1/100/1000项、20轮，集合完整、对象IO=0；native小目录p95为75.12/71.65/79.89ms，冷认证最大93.99ms；1000项227.87ms另列 |
| 维护工具 | 8组合成验收，包括真实网络暂停期间并发写/CAS、低id重扫、预算中断、进程中断清理、恢复、默认dry-run零写和输出哨兵 |
| K2负控 | 24实际变异均编译成功且对应断言实际退出1；恢复前后正控通过 |
| K1b传输 | nginx1.31.3 full：93协议、54错误GET/HEAD、12实际负控、TLS/取消/不完整体/进展超时通过；1.24.0协议和负控也通过 |
| P1 | 冷/热各10轮交替；candidate正文TTFB p95=96.29/90.64ms，JVM=245.64/13.23ms，stage=1828.98/1808.23ms，均满足预设双阈值 |
| P2 | 单/四路256/512MiB完整摘要，两跳nginx/native/curl/Unix fd同范围；临时正文增量0，PSS增长每请求为-195/3.5KiB，未把共享内核内存说成精确归因 |
| 原S8 | native entry完整256/512MiB上传/既有FM暂存下载、摘要、RSS、断连清理和原负控通过；CI继续原实际systemd资源门 |
| 静态守卫 | core80源无Java、4负控，FFI、配置默认/7变异、Ruff、Dawn格式、身份/trailer与FM85锚点/51基线通过 |

副本及真实源等证据仍分别为 PRODUCTION_PENDING。本机 HEAD 采集覆盖和最终 wire 一致，
只证明 synthetic profile；不代表真实源 HEAD/GET 等价，更不代表 stat 推导已成立。
Finder/Windows、实际生产资源限制、全部旧 token 失效和正式 S13 原子切换均未执行。
production blockers 非空，故 production_gate=BLOCKED。

可复核的净化摘要：

- [两臂及PROPFIND规模](evidence/download-k2-dual-arm.json)
- [合成副本](evidence/download-k2-backfill.json)
- [24实际负控](evidence/download-k2-negatives.json)
- [nginx1.31.3完整门](evidence/download-k2-nginx-1.31.3-full.json)
- [nginx1.24.0协议门](evidence/download-k2-nginx-1.24.0-protocol.json)

最终 head 的 CI 另行提供全部 job 与161变异覆盖汇总。此处本地摘要不冒充 CI 或生产放行。
维护安全默认值、收据要求及服务器/S13逐项交接见[维护清单](download-k2-maintenance.md)。

原 `fm-save-codepoint-size` 变异在 K2 长度校验下先拒绝发布，因此精确红集从原字节数owner
扩为同次保存的 status/calls/byte-size 三项，原owner保留，严格要求三项齐全且无其他差异。
原 timeout 分类变异保留新增 HEAD owner 仍使用的 timeout import，继续实际编译和断言变红。
Jammy 包合同使用私有checksum钉住的nginx1.31.3，避免系统旧版本不支持测试启动选项。
原过期token变异还必须使新增普通和分片实际消费案例变红，精确要求原owner加这两项。
K2验证模式把失败断言记为FAIL以完成机器报告；record模式及隔离负控仍立即抛出固定case名，
禁止把失败断言重录为新golden。
