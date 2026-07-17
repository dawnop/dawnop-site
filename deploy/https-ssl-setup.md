# HTTPS / SSL 证书体系说明

> 本文记录 dawnop.com 全站 HTTPS 的架构、三方（我们的源站 / 七牛 / 腾讯）协作关系、
> 证书体系与自动续期机制，以及一次性搭建时的关键技术细节。
>
> **约定**：文中服务器地址一律写作 `<SERVER_IP>`、SSH 用户写作 `<user>`（真实值见本地私有记录，不入库）；
> 密钥、certID 等以占位或示例呈现。所有敏感值只存在服务器 `backend/.env` 与 acme.sh 配置里，不进仓库。

---

## 1. 三方角色与协作关系

整站涉及三个 HTTPS 终结点 / 三方参与者：

| 参与方 | 承担的域名 | HTTPS 在哪里终结 | 职责 |
|--------|-----------|-----------------|------|
| **我们的源站**（Nginx + Dawn 后端） | `dawnop.com` / `www.dawnop.com` | 源站 Nginx | 托管前端 SPA（`dist/`）、反代 `/api` 到后端；持有站点私钥 |
| **七牛**（Kodo 私有空间 + 融合 CDN） | `storage.dawnop.com` | 七牛 CDN 边缘节点 | 存储文件本体（图片/文本），对外以签名 URL 直连下发；CDN 边缘做 TLS 终结 |
| **腾讯云**（DNSPod + 云主机 + 曾经的 SSL） | `dawnop.com` 整个 DNS 区 | —— | ① DNSPod 是权威 DNS（所有解析记录 + DNS-01 挑战自动化）；② 云主机（安全组/443）；③ **曾经**签发过 TrustAsia DV 证书，现已停用 |

### 1.1 为什么要三方协作

- **文件不走源站**：文件管理（自建 `FilesLabView` + `api/fmApi.js`）的上传是「前端直传七牛」，预览/下载是「后端 302 跳到七牛签名 URL、浏览器直连七牛取字节」。好处是**省源站带宽**——大文件流量全部由七牛承担，源站只做鉴权和签名。
- **代价**：文件字节由 `storage.dawnop.com`（七牛）提供，页面由 `dawnop.com`（源站）提供，于是**两个 origin 都必须是 HTTPS**，且需要 CORS 打通（见 §5）。

### 1.2 HTTPS 数据流

```
 ┌── 页面加载 ───────────────────────────────────────────────┐
 │  浏览器 ──https──▶ dawnop.com (Nginx, LE 证书)             │
 │                     ├─ 静态 SPA (dist/)                    │
 │                     └─ /api ─反代─▶ 127.0.0.1:8001 (Dawn)   │
 └───────────────────────────────────────────────────────────┘

 ┌── 文件预览 / 下载 ────────────────────────────────────────┐
 │  浏览器 ──https──▶ dawnop.com/api/fm/preview (带 token 鉴权)│
 │        ◀── 302 重定向到七牛签名 URL ──                      │
 │  浏览器 ──https──▶ storage.dawnop.com (七牛 CDN, LE 证书)   │
 │                     └─回源─▶ Kodo 私有空间 dawnop           │
 │                                                            │
 │  ※ 文本在线预览例外：不走 302（跨域 302 会把 Origin 变 null,│
 │    命不中七牛 CORS），改由前端 fetch 签名 URL 直连七牛，     │
 │    因此七牛需配 CORS 放行 https://dawnop.com（见 §5）        │
 └───────────────────────────────────────────────────────────┘
```

---

## 2. 为什么整站都要 HTTPS（混合内容问题）

主站 `https://dawnop.com` 是 HTTPS 后，浏览器会**拦截页面内的 HTTP 子资源**（mixed content）。文件预览/下载用的七牛签名 URL 若是 HTTP，就会被拦、功能失效。

七牛的**测试域名**（形如 `tgzrs40ih.hd-bkt.clouddn.com`）是 **HTTP、限速、约 30 天回收**，本地开发够用，但生产不可用。因此必须给私有空间**绑定一个自定义 HTTPS 域名** = `storage.dawnop.com`，这就是七牛侧 HTTPS 的由来。

---

## 3. 证书策略：Let's Encrypt 通配符 + acme.sh

### 3.1 为什么用通配符 + Let's Encrypt（而非腾讯 DV）

| 维度 | 腾讯 TrustAsia DV（旧） | Let's Encrypt 通配符（现） |
|------|------------------------|---------------------------|
| 覆盖 | 每个域名单独申请（@/www 一张，cdn/storage 还要各申请） | 一张 `*.dawnop.com` 覆盖所有子域 |
| 续期 | 手动，约 1 年 | acme.sh **全自动**，90 天周期 |
| 免费通配符 | 受限（部分需企业信息） | 免费 |
| 结果 | 到期要人工续、易忘 | 无人值守 |

一张 `dawnop.com` + `*.dawnop.com` 通配符即可同时覆盖主站、`www`、`storage`（及将来的 `cdn`），只维护一处、只续一次。

### 3.2 为什么必须走 DNS-01 挑战

**通配符证书只能用 DNS-01 验证**（HTTP-01 无法验证 `*`）。DNS-01 的做法是：CA 给一个随机值，你把它写到 `_acme-challenge.dawnop.com` 的 TXT 记录，CA 查到即认可你控制该域。

acme.sh 的 `dns_tencent` 插件把这一步**全自动化**：签发/续期时通过 DNSPod API **自动添加 `_acme-challenge` TXT，验证完自动删除**——无需人工碰 DNS。

### 3.3 签发命令（一次性）

```bash
# 腾讯云 API 凭证仅在本次会话以环境变量提供，用完即弃；acme.sh 会把它们
# 存进 ~/.acme.sh/account.conf（SAVED_Tencent_*）供日后自动续期复用。
export Tencent_SecretId="<id>"
export Tencent_SecretKey="<key>"
~/.acme.sh/acme.sh --issue --server letsencrypt \
  -d dawnop.com -d '*.dawnop.com' --dns dns_tencent
```

产物（服务器）：
- 证书链 `~/.acme.sh/dawnop.com_ecc/fullchain.cer`
- 私钥 `~/.acme.sh/dawnop.com_ecc/dawnop.com.key`
- 类型 **ECC（ec-256）**，Let's Encrypt 签发，90 天有效期

---

## 4. 两侧 HTTPS 落地

### 4.1 源站 Nginx（dawnop.com / www）

Nginx 80 → 443 强制跳转，443 用通配符证书。证书由 acme.sh 的 `--install-cert` 落到固定路径（Nginx 配置因此不用改）：

```bash
# 证书目录 chown 给 <user>，使 acme.sh（以 <user> 运行）与续期 cron 能覆盖写入
sudo chown -R <user>:<user> /etc/ssl/dawnop

~/.acme.sh/acme.sh --install-cert -d dawnop.com --ecc \
  --key-file       /etc/ssl/dawnop/dawnop.com.key \
  --fullchain-file /etc/ssl/dawnop/dawnop.com_bundle.crt \
  --reloadcmd      "chmod 600 /etc/ssl/dawnop/dawnop.com.key && sudo systemctl reload nginx && python3 ~/qiniu-cert-deploy.py >> ~/qiniu-cert-deploy.log 2>&1"
```

Nginx 相关片段在私有运维笔记 `~/workspace/dawnop-ops/nginx.conf`（nginx 全配 2026-07-18 移出公开仓）：`ssl_certificate` 指向 `dawnop.com_bundle.crt`（fullchain），`ssl_certificate_key` 指向 `dawnop.com.key`，`ssl_protocols TLSv1.2 TLSv1.3`。`www` 靠证书 SAN `*.dawnop.com` 一并覆盖。

> **前置基建**：腾讯云安全组需放行 **TCP 443**（一次性；初次配 HTTPS 时曾因未放行导致连接超时）。

### 4.2 七牛（storage.dawnop.com）

私有空间绑自定义 HTTPS 域名，在七牛侧**本质是创建一个融合 CDN 加速域名**，回源到私有 bucket，边缘挂证书。步骤：

1. **上传证书** → `POST https://api.qiniu.com/sslcert`（QBox 鉴权），body `{name, common_name:"*.dawnop.com", pri:私钥, ca:fullchain}` → 返回 `certID`。
2. **域名归属权验证**：DNSPod 加一条 `verification` TXT（值 `verify_...`），七牛校验通过。
3. **控制台绑定域名**（唯一需人工的一步）：加速域名 `storage.dawnop.com`、通信协议 HTTPS、选上传的证书、开**强制 HTTPS**、回源本空间 `dawnop`。提交后七牛给一个 **CNAME 目标**（形如 `storage-dawnop-com-xxxxxx.qiniudns.com`）。
   - **缓存配置**：`缓存参数 = 保留所有参数` + `缓存时间 = 短/遵循源站`。原因见 §6.3。
4. **加 CNAME 解析** → DNSPod `storage` CNAME → 七牛给的目标。
5. **配 CORS**（见 §5）。
6. **切换后端** → 生产 `/opt/dawnop/backend/.env` 的 `QINIU_DOMAIN=https://storage.dawnop.com`，
   重启 `dawnop-dawn`（M6 后生产是 Dawn；它经 `DAWNOP_ENV` 读的就是这个 .env）。

验证：`curl https://storage.dawnop.com/` 返回 **HTTP 403**（未签名访问私有空间的正常响应）+ TLS 校验通过，即整条打通。

---

## 5. CORS：为什么、配什么

- **图片预览、文件下载**：走 302 重定向，浏览器跟随即可，**不需要 CORS**。
- **文本/代码在线预览**：**不能**走 302——跨域重定向会把请求 `Origin` 变成 `null`，命不中七牛 CORS。故前端 `api/fmApi.js` 的取内容逻辑：先 `GET /api/fm/sign` 拿签名 URL，再用 `fetch` **直连七牛**（Origin 正常），此时**必须** CORS 放行；直连被拦时回退 `GET /api/fm/content` 后端代理。

七牛 bucket CORS（`POST https://uc.qiniuapi.com/corsRules/set/<bucket>`，QBox 鉴权）：

```json
[{ "allowed_origin": ["https://dawnop.com"],
   "allowed_method": ["GET", "HEAD"],
   "allowed_header": ["*"], "exposed_header": [], "max_age": 3600 }]
```

> **坑：来源逐字匹配**。`http://localhost:5173` 与 `http://127.0.0.1:5173` 是两个来源；生产 `https://dawnop.com` 与带 `www` 的也不同。按实际访问来源逐条加。

---

## 6. 关键技术细节

### 6.1 七牛管理 API 的 QBox 鉴权

`Authorization: QBox <AccessKey>:<sign>`，其中
`sign = urlsafe_base64( HMAC-SHA1( SecretKey, signingStr ) )`，
`signingStr = <path>[?<query>] + "\n"`（**JSON body 不计入签名**；仅当 `Content-Type: application/x-www-form-urlencoded` 时才把 body 追加进去）。

用到的七牛管理 API：
| 用途 | 方法 & 路径 |
|------|-------------|
| 上传证书 | `POST api.qiniu.com/sslcert` → `{certID}` |
| 改域名 HTTPS 配置（换绑证书） | `PUT api.qiniu.com/domain/<name>/httpsconf` `{certId, forceHttps, http2Enable}` |
| 设 bucket CORS | `POST uc.qiniuapi.com/corsRules/set/<bucket>` |

### 6.2 腾讯云 DNSPod API 的 TC3-HMAC-SHA256 鉴权

DNSPod v3（`Version: 2021-03-23`，`Service: dnspod`，Host `dnspod.tencentcloudapi.com`）。签名分四步：
1. 拼 **CanonicalRequest**（method / uri / query / headers / signedHeaders / sha256(body)）。
2. 拼 **StringToSign**（`TC3-HMAC-SHA256` / 时间戳 / `<date>/dnspod/tc3_request` / sha256(CanonicalRequest)）。
3. 逐层 HMAC 派生签名密钥：`HMAC("TC3"+SecretKey, date) → HMAC(_, "dnspod") → HMAC(_, "tc3_request")`，再对 StringToSign 求 HMAC。
4. 组 `Authorization: TC3-HMAC-SHA256 Credential=.../<scope>, SignedHeaders=content-type;host, Signature=...`。

用到的 Action：`CreateRecord`（加记录）、`DescribeRecordList`（列记录）、`DeleteRecord`（删记录）。
> **注意**：v3 的 Action 是 `CreateRecord`，不是老 API 的 `RecordCreate`（初次踩过 `InvalidAction`）。`RecordLine` 用中文 `"默认"`。

### 6.3 缓存参数为何选「保留所有参数」

私有空间签名 URL 形如 `…/key?e=<过期>&token=<签名>`，token 是唯一凭证。
- 若「**忽略参数**」：CDN 只按路径做缓存 key。文件一旦被缓存，**TTL 内任何人只要知道路径、即使不带/带错 token 也会命中缓存**——等于绕过私有签名，泄露私有内容。
- 选「**保留所有参数**」：带不同 token 算不同缓存 key，token 不可猜，等于每条授权链接各缓存各的，不会漏给未签名者。命中率低，但私有后台正确性/安全 > 命中率。
- 配合**短 TTL / 遵循源站**：后台改了文件能立刻看到新版本。

---

## 7. 自动续期机制（全自动，含七牛侧）

acme.sh 装了 cron（每天检查），通配符证书约到期前 30 天自动续。续期成功后 `reloadcmd` 触发**两侧同步**：

```
acme.sh --cron（每天 04:36）
  └─ 续期 dawnop.com + *.dawnop.com
       └─ 安装新证书到 /etc/ssl/dawnop/
       └─ reloadcmd:
            chmod 600 dawnop.com.key
            sudo systemctl reload nginx            # ← 源站 dawnop.com/www 即时生效
            python3 ~/qiniu-cert-deploy.py         # ← 七牛侧同步
              ├─ POST /sslcert 上传新证书 → 新 certID
              └─ PUT /domain/storage.dawnop.com/httpsconf 改绑新 certID
              （输出追加到 ~/qiniu-cert-deploy.log）
```

- `~/qiniu-cert-deploy.py`（服务器 `/home/<user>/`）：读 `backend/.env` 里的七牛 AK/SK 与 `/etc/ssl/dawnop/` 的新证书，上传并改绑。**幂等**：每次上传生成一张新证书对象 `dawnop-wildcard-<日期>`（旧的不再引用、无害；一年约 6 张，可选偶尔在控制台清理）。
- 排查某次续期：看服务器 `~/qiniu-cert-deploy.log`。

> 全链路无人值守：源站与七牛两侧证书都自动更新，运维零介入。

---

## 8. 敏感信息处理原则

- **腾讯云 API 凭证**：仅在搭建会话以 `scp` 临时上传 + 环境变量使用，用完 `shred`；长期只由 acme.sh 存于 `~/.acme.sh/account.conf`（`SAVED_Tencent_*`，权限 600）供续期。**从不进仓库、不打印**。
- **七牛 AK/SK**：只在服务器 `backend/.env`（不提交）；脚本运行时读取，不硬编码。
- **私钥**：源站 `/etc/ssl/dawnop/dawnop.com.key`（600）与 acme.sh 目录；上传七牛的那份供其边缘 TLS 终结。
- 旧腾讯 TrustAsia 证书已停用，备份于 `/etc/ssl/dawnop/*.tencent.bak`（可回滚）。

---

## 9. 验证与排查速查

```bash
# 看某主机实际生效的证书（issuer 应为 Let's Encrypt）
echo | openssl s_client -servername dawnop.com -connect dawnop.com:443 2>/dev/null \
  | openssl x509 -noout -issuer -subject -dates

# 七牛侧连通（未签名访问返回 403 + TLS 校验 0 即正常）
curl -sS -o /dev/null -w "HTTP=%{http_code} TLS=%{ssl_verify_result}\n" https://storage.dawnop.com/

# acme.sh 续期计划 / cron
~/.acme.sh/acme.sh --list
crontab -l | grep acme

# DNS 现状（经 DNSPod 公共解析）
dig +short dawnop.com A @119.29.29.29
dig +short storage.dawnop.com CNAME @119.29.29.29
```

**当前应有的 DNS 记录**（其余验证残留 `_dnsauth*`、`verification` 已清理）：

| 记录 | 类型 | 值 |
|------|------|-----|
| `@` | A | `<SERVER_IP>` |
| `www` | A | `<SERVER_IP>` |
| `storage` | CNAME | `storage-dawnop-com-xxxxxx.qiniudns.com` |
| `@` | NS | DNSPod 系统 ×2 |

---

## 10. 一句话总结

一张 **Let's Encrypt 通配符证书**（acme.sh + DNSPod DNS-01 签发、自动续期）同时武装**源站 Nginx**（`dawnop.com`/`www`）与**七牛 CDN**（`storage.dawnop.com`）两个 HTTPS 终结点；续期后经 `reloadcmd` + `qiniu-cert-deploy.py` 自动同步到两侧。腾讯云在其中只担任**权威 DNS（DNSPod）** 与云主机角色，其自家的 DV 证书已退役。
