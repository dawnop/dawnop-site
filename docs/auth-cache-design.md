# WebDAV 认证缓存与短租约

状态：S6 已实现，双臂验收与负控已接入 CI。工具链仍为 v0.97.0；JVM 为生产入口，native 只消费同一协议作验收。

## 密钥、键与账号版本

缓存键为 HMAC-SHA256(SECRET_KEY, `dawnop.dav.credentials.v2\0` + 原始 UTF-8 `user:pass`)，存于 runtime_cache 的 dav.auth.v2 命名空间。SECRET_KEY 来自现有 backend/.env，沿用现有装载方式；所有共库进程必须使用同一值。不得写入原凭据、Authorization 或裸 SHA 摘要。凭据版本另以域分离 HMAC 绑定账号 id、用户名、当前 password_hash 与 created_at，不复制 password_hash 到缓存。缓存 payload 只有版本标记。

轮换 SECRET_KEY 后，新进程计算的新键立即不认可旧条目，JWT 也按原规则失效；轮换必须同时替换全部服务臂，结束仍持旧密钥的进程。缓存无法撤销尚未退出的旧密钥进程。过期条目在 miss 写入时有界清理。旧 dav.v1 槽在初始化时删除。

users 没有停用字段；新增可选 auth_account_state(user_id, enabled)，缺行按 enabled=1，写 enabled=0 即停用 WebDAV。账号删除、改名、改密码和停用都由每次命中的 users 联查即时识别。停用不改变既有 JWT 语义，不扩展后台 UI。账号版本使用当前 hash 派生，直接 SQL 改密码也生效，不依赖写者记得增加计数。

命中用单次 SELECT 联查缓存、users 和状态表，验证 300000ms 严格边界及版本。只读，不续期、不写计数。缺新增表为 miss，其他 SQL 错误传播并拒绝认证。恢复旧库后在下一次 claim 的短事务中重建新增表。

## 租约协议

auth_verify_leases 的主键为 HMAC 凭据键；随机 owner token 防止过期持有者发布或删除接管者结果。租期 5s，等待用进程单调时钟检查 15s 重试预算（单次 SQLite BUSY 仍沿用 5s 上限），轮询 25ms；持久期限使用墙钟，早于 claimed_ms 的倒退按失效处理。首次 claim 与每次接管都在 BEGIN IMMEDIATE 内重读成功缓存和账号版本，仅短事务写入租约。bcrypt、随机数生成和 sleep 均在事务外。

验证结束在第二个短事务中复核当前账号版本、enabled、owner 与租期，才发布 300s 成功缓存；旧版本或过期持有者不能发布。失败不写成功缓存；短租约暂留失败结果供同一并发群体重读，租期到后允许重新尝试。持有者崩溃由租约到期后接管恢复，数据库写锁不会覆盖 bcrypt。等待超时和数据库失败均拒绝认证。已知账号才占租约，活跃租约最多 128 行、成功条目最多 256 行；claim 清理过期行，达到租约上限拒绝新 miss。

## 验收

同源场景分别编入 JDBC/native，检查键的独立 HMAC 参考向量、密钥轮换、300s 边界、改密码/停用/删除、只读命中、bcrypt 不在事务内、过期持有者 fencing。真实 40 个进程并发 miss 用 verifier 调用计数并执行真实 cost12 bcrypt，覆盖两臂与混合臂（本机三组均为一次）；独立进程交叉命中不增加计数。杀死实际持有者后必须恢复，另测密码在 bcrypt 期间改变、未知/错误凭据喷洒与租约容量。六种负控均先成功编译后按专属断言红（取消互斥时两臂错误凭据各产生 40 次 bcrypt）：明文键、裸 SHA、旧密码命中、到期命中、取消互斥、永不接管。

原 355 golden、7 skip、160 变异、Basic/流式 guard、S4/S5 验收及 fmt/core 零 Java 都继续执行；CI 新步骤报告墙钟，不提高 timeout。

## 不做的（理由）

不调 TTL 或 bcrypt 成本；不增加 native HTTP 入口；不改 JWT/业务输出；不部署或访问服务器；不改 SQLite 引擎；不提供常驻锁或进程内互斥，因为不能跨一连接一进程生效。
