# 下载表示持久化 K2

协调裁决 D1 至 D7 已固定。本刀仅使用合成 SQLite 和回环假对象；生产门由各项证据计算，服务器证据未取得时保持 BLOCKED。

## K2a 数据与采集

`files.size` 是完整表示长度的唯一真值。显式 additive migration 新增 nullable 的
`representation_kind/key/profile/etag/last_modified_s`，不改旧值。目录允许空字段；旧文件缺值属于 Missing 迁移状态。新发布必须经过统一校验器构造 Published，仓库写入口同时校验 key 和 size。

非空表示为 `object-identity-v1`，profile 绑定规范化下载 authority、identity 和无变换版本。
空表示为 `local-empty-v1`，强标签固定，发布时间持久化；不得据旧 size 判空。
写入先完成准入预检和对象写入，再在事务外对实际下载 URL 发无条件 identity HEAD。
HEAD 与 handoff 共用 key 编码和签名，只接受 200、唯一合法 ETag/Last-Modified/完整长度及无编码变换。
普通上传 token 使用 insertOnly，COPY 不覆盖已有 key，所有覆盖写使用新 key。

原有即时事务继续拥有行、账本、配额和收据。采集失败不进入提交；直传保留 pending 以便重试，应用拥有的新对象按现有引用规则回收。COPY 对每个目标采集，整树准备完才原子提交；源快照比较包括表示字段。MOVE/rename 保留表示且不访问对象。

## K2b 同代读合同

GET/HEAD/PROPFIND 是单个部署能力组，关闭或表示缺失/错配时鉴权后 503，固定净化错误，无对象 IO。非空 GET/HEAD 经 K1b 两跳 nginx，每次仅一笔对应方法；PROPFIND 纯 DB，整请求预检后输出，精确保留 ETag 和文件 LM。

local-empty 共享纯 Dawn 条件求值顺序：If-Match，缺少 If-Match 时 IUS，INM，缺少 INM 时 IMS。标签强弱比较、列表和通配符分别处理；非法日期忽略。重复或非法条件标签拒绝为 400。Range/If-Range 显式忽略，正常结果 200、长度 0；条件可得 304/412，HEAD 无体。

golden 修改前列旧 case 对照，再按新合同重录；不归一化文件验证器或 LM，不增加无理由 skip。raw application wire 另验唯一头，不能用 nginx 合并头掩盖错误。

## K2c 维护与交付

一次性维护使用共享 Dawn Db/Upstream，Python 仅编排。默认 dry-run，显式身份，replica-first 收据，路径/realpath/硬链接拒绝，keyset 分页、事务外网络、CAS、短事务、私有检查点及末次全扫。输出只准版本、指纹、阶段、计数、时延类别和判词，异常及子命令日志同样净化。

服务器内真实源 HEAD/GET 等价、stat 推导、全量副本、真实客户端、生产资源限制和 S13 切换分别列门；本地通过不能替代这些证据。S13 必须升级全部 writer、停发并失效旧 token、冻结和排空写入与三方法后同代开放。回退恢复对应整包与入口，保留新增列，不恢复旧 DB 快照；旧 writer 再写后 ready 作废。
