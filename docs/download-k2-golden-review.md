# K2 golden 逐项变更评审

执行前基线：read 58、edge 102、webdav 104、qiniu 91，每臂 355，七个 skip。基线双臂已通过。下列对照先于本刀重录写入。

read 与 edge 无差异，不重录。webdav 无对象配置，三方法能力关闭后鉴权成功返回 503；路径、Depth、Basic、写方法及 raw guard 合同保留。qiniu 通过同一隔离 nginx 跑真实写入和三方法闭环。

| 原 case | 新合同与原因 |
| --- | --- |
| `webdav:propfind.root.d0` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.root.d1` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dir.d1` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.file.d0` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.unicode` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.404` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.prefix` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.depth.invalid.fail-closed` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:head.file` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:head.dir` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:head.404` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.sandbox` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.move.dot.source` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.moved.old` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.moved.new` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.copy.dotdot.source` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.scheme.rejected` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.authority.rejected` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.host.rejected` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:copy.dest.host.singleton.fail-closed` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.prefix.partial` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.prefix.outside` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.prefix.reserved-slash` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.query.rejected` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.fragment.rejected` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.utf8.no-replacement-alias` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.file-parent.rejected` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.path-absolute` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.absolute-http` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.absolute-https` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.prefix-unreserved.path` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.prefix-unreserved.absolute` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.authority-normalized` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.double-slash` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.trailing-slash` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.root-prefix` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.encoded-question` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.encoded-fragment` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.utf8.multibyte` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.utf8.replacement-character` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.copied` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.hidden` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.encoded.dot` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.dest.plus` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `webdav:propfind.deleted` | 鉴权后能力组关闭，503，无对象 IO；附属存在性观察随该方法变化。 |
| `qiniu:copy.subdir.tree` | 精确对象 ETag/文件 LM/完整长度，写入增加目标 HEAD；唯一头以最终 wire 为准。 |
| `qiniu:copy.subdir.tree.sub` | 精确对象 ETag/文件 LM/完整长度，写入增加目标 HEAD；唯一头以最终 wire 为准。 |
| `qiniu:copy.subdir.objects` | 登记、上传或 COPY 提交前增加 HEAD，调用顺序与新 key 关系保留。 |
| `qiniu:copy.subdir.bytes` | 精确对象 ETag/文件 LM/完整长度，写入增加目标 HEAD；唯一头以最终 wire 为准。 |
| `qiniu:copy.file.calls` | 登记、上传或 COPY 提交前增加 HEAD，调用顺序与新 key 关系保留。 |
| `qiniu:get.file` | 精确对象 ETag/文件 LM/完整长度，写入增加目标 HEAD；唯一头以最终 wire 为准。 |
| `qiniu:get.file.range` | 精确对象 ETag/文件 LM/完整长度，写入增加目标 HEAD；唯一头以最终 wire 为准。 |
| `qiniu:get.file.302` | 精确对象 ETag/文件 LM/完整长度，写入增加目标 HEAD；唯一头以最终 wire 为准。 |
| `qiniu:get.file.calls` | 登记、上传或 COPY 提交前增加 HEAD，调用顺序与新 key 关系保留。 |
| `qiniu:put.new.calls` | 登记、上传或 COPY 提交前增加 HEAD，调用顺序与新 key 关系保留。 |
| `qiniu:put.new.bytes` | 精确对象 ETag/文件 LM/完整长度，写入增加目标 HEAD；唯一头以最终 wire 为准。 |
| `qiniu:put.overwrite.calls` | 登记、上传或 COPY 提交前增加 HEAD，调用顺序与新 key 关系保留。 |
| `qiniu:put.overwrite.bytes` | 精确对象 ETag/文件 LM/完整长度，写入增加目标 HEAD；唯一头以最终 wire 为准。 |
| `qiniu:fm.copy.subdir.objects` | 登记、上传或 COPY 提交前增加 HEAD，调用顺序与新 key 关系保留。 |
| `qiniu:fm.createfile.calls` | 登记、上传或 COPY 提交前增加 HEAD，调用顺序与新 key 关系保留。 |
| `qiniu:fm.save.calls` | 登记、上传或 COPY 提交前增加 HEAD，调用顺序与新 key 关系保留。 |
| `qiniu:fm.register.ok` | 成功输入改为与 stat 一致的长度；冲突输入由新闭环另外断言拒绝并保留 pending。 |
| `qiniu:fm.register.calls` | 登记、上传或 COPY 提交前增加 HEAD，调用顺序与新 key 关系保留。 |
| `qiniu:copy.overwrite.calls` | 登记、上传或 COPY 提交前增加 HEAD，调用顺序与新 key 关系保留。 |
| `qiniu:copy.overwrite.tree` | 精确对象 ETag/文件 LM/完整长度，写入增加目标 HEAD；唯一头以最终 wire 为准。 |
| `qiniu:copy.overwrite.tree.sub` | 精确对象 ETag/文件 LM/完整长度，写入增加目标 HEAD；唯一头以最终 wire 为准。 |
| `qiniu:copy.overwrite.target.bytes` | 精确对象 ETag/文件 LM/完整长度，写入增加目标 HEAD；唯一头以最终 wire 为准。 |
| `qiniu:copy.overwrite.source.retained` | 精确对象 ETag/文件 LM/完整长度，写入增加目标 HEAD；唯一头以最终 wire 为准。 |
| `qiniu:move.overwrite.tree.sub` | 精确对象 ETag/文件 LM/完整长度，写入增加目标 HEAD；唯一头以最终 wire 为准。 |
| `qiniu:move.overwrite.target.bytes` | 精确对象 ETag/文件 LM/完整长度，写入增加目标 HEAD；唯一头以最终 wire 为准。 |
| `qiniu:fm.save.utf8.calls` | 登记、上传或 COPY 提交前增加 HEAD，调用顺序与新 key 关系保留。 |
| `qiniu:fm.persisted-mime.fail-safe` | 精确对象 ETag/文件 LM/完整长度，写入增加目标 HEAD；唯一头以最终 wire 为准。 |
| `qiniu:fm.upload-token.deadline-window` | 普通凭证新增 insertOnly，实际花费与不同字节重放均验证。 |

迁移：`webdav:head.file` 的成功覆盖移至 `qiniu:head.file`，plain 改名 `head.file.unconfigured`；`qiniu:get.file.302` 改名 `get.file.all-ua`，所有 UA 返回同源字节。未新增 skip。

新增独立闭环：`k2.dav-three-methods`、`k2.conditions-and-ranges`、`k2.missing-and-mismatched-metadata`、`k2.head-failure-preserves-old-row`、`k2.empty-owner-and-transitions`、`k2.ordinary-token-replay-and-register`。这些 case 在记录 PASS 前实际断言 DB、最终 wire、体摘要和对象调用数；不是手填通过值。

删除的旧 `range_pair` 单测只服务已删除的应用远程 Range 引擎；bounded/open/suffix/越界/畸形/多段现在由实际 nginx 闭环逐项观察。

七个 skip 逐条保留：
- `read:fm.stats`：needs QINIU_* credentials (live bucket usage)
- `edge:fm.stats.ok`：needs QINIU_* credentials (live bucket usage)
- `webdav:get.file`：GET streams object bytes ,  covered in contract_qiniu.py (fake bucket); unconfigured here
- `webdav:put.new`：PUT uploads object bytes ,  covered in contract_qiniu.py (fake bucket); unconfigured here
- `webdav:copy.file`：COPY of a file duplicates the object ,  covered in contract_qiniu.py (fake bucket); unconfigured here
- `webdav:delete.file`：DELETE of a file removes the object ,  covered in contract_qiniu.py (fake bucket); unconfigured here
- `qiniu:fm.stats`：bucket usage comes from the qiniu billing/space APIs (qiniu_stats.dawn), which this fake does not model ,  needs QINIU_* credentials

后续新增（再次重录前固定）：`k2.copy-move-and-writer-coverage` 核对 COPY 目标日期、MOVE/rename 无外联及 drop/代理写入表示；`k2.metadata-header-validation` 验缺失/重复/非法头、弱标签、1xx 最终块与非法 stat；`k2.resumable-token-replay` 实际发送 v2 初始化、两片与完成重放；`k2.propfind-scale` 对 0/1/100/1000 项逐 href 校核完整与零外联，独立保留计时证据。空读取跨秒后仍核对持久 LM。

S10 原直接 DAV stage 大体测量迁至 capability off 的 FM content stage：DAV off 已决定为 503，继续通过旧 DAV 行为测 stage 将违反裁决。保留原 256/512 MiB 摘要、RSS 阈值、上传和断连门；新 DAV 数据面的大体证据仍由同一两跳 nginx 下载矩阵提供。直接应用 wire 继续核对 off HEAD 与 local-empty GET/HEAD 的唯一长度。
S12 的实际离线 systemd 资源门同样把默认 off 的512MiB下载改由 FM content 的既有 stage 路径，
上传仍为 DAV PUT，摘要/内存/进展/限额/loader 检查不变。真实 JVM 回退读同库的断言通过鉴权 FM
列表读取 native 创建的目录；新包 off 的 DAV 三读方法按 D3 返回503。隔离测试 unit 显式信任本机假 CA。
