# Vaultwarden 部署（vault.dawnop.com）

自建 Bitwarden 兼容密码管理服务。与 dawnop-site 后端**完全独立**：单独的 Docker 容器，
只监听 `127.0.0.1:8222`，公网入口由 nginx 子域名 `vault.dawnop.com` 反代（复用通配符证书 `*.dawnop.com`）。

## 0. 前置

- DNS：`vault.dawnop.com` A 记录 → `<server>`（备案随主域继承）。
- 服务器已装 Docker + compose v2（已确认）。
- 通配符证书 `/etc/ssl/dawnop/dawnop.com_bundle.crt`（acme.sh 自动续，已覆盖 `*.dawnop.com`）。

## 1. 传部署产物到服务器

```bash
# 本机
scp deploy/vaultwarden/docker-compose.yml deploy/vaultwarden/.env.example \
    <user>@<server>:/tmp/vw/
ssh <user>@<server>
sudo mkdir -p /opt/vaultwarden
sudo cp /tmp/vw/docker-compose.yml /opt/vaultwarden/
sudo cp /tmp/vw/.env.example /opt/vaultwarden/
sudo chown -R dawn:dawn /opt/vaultwarden
cd /opt/vaultwarden
```

## 2. 生成 ADMIN_TOKEN 并写 .env

```bash
# 生成 argon2 哈希令牌（记住输入的明文口令——登录 /admin 用它，不是这串哈希）
docker run --rm -it vaultwarden/server:1.36.0-alpine /vaultwarden hash
# 复制输出的整串 $argon2id$v=19$...

cp .env.example .env
# 编辑 .env：
#   SIGNUPS_ALLOWED=true          # 先开，注册完自己账号后改回 false
#   ADMIN_TOKEN='$argon2id$v=19$...'   # 单引号包住，$ 不转义
nano .env
```

## 3. 拉镜像（⚠️ 这台服务器拉不动 Docker Hub）

腾讯云机器直连 `registry-1.docker.io` 被墙（daemon.json 里的腾讯 pull-through 镜像只能返回
manifest、拉 layer 仍回退官方源而超时）。**用公共代理拉、再打回原 tag**（compose 里写的仍是官方 tag）：

```bash
sudo docker pull docker.m.daocloud.io/vaultwarden/server:1.36.0-alpine
sudo docker tag  docker.m.daocloud.io/vaultwarden/server:1.36.0-alpine vaultwarden/server:1.36.0-alpine
sudo docker rmi  docker.m.daocloud.io/vaultwarden/server:1.36.0-alpine   # 去掉临时 tag
```

> 升级换版本号时同理：先按新 tag 走这三步，再改 `docker-compose.yml` 的 image、`up -d`。
> daocloud 挂了可换 `docker.1panel.live` / `hub.rat.dev`（均实测可通）。docker 需 `sudo`（dawn 不在 docker 组）。

## 4. 启动

```bash
cd /opt/vaultwarden
sudo docker compose up -d
sudo docker compose logs -f       # 看到 "Rocket has launched" 即就绪，Ctrl-C 退出
curl -sf http://127.0.0.1:8222/alive && echo OK   # 健康检查
```

## 5. 配 nginx

`deploy/nginx.conf` 已含 `server vault.dawnop.com` 块和 `map $http_upgrade $vault_connection_upgrade`。
把更新后的 nginx.conf 覆盖到服务器（**先备份**）：

```bash
sudo cp /etc/nginx/sites-available/dawnop /tmp/dawnop.nginx.bak.$(date +%s)
# 传新的 nginx.conf 后：
sudo cp /tmp/nginx.conf /etc/nginx/sites-available/dawnop
sudo nginx -t && sudo systemctl reload nginx
```

## 6. 首次注册 → 关闭注册

1. 浏览器开 `https://vault.dawnop.com`，**Create account** 注册你自己的账号。
2. 回服务器：`.env` 改 `SIGNUPS_ALLOWED=false`，然后 `sudo docker compose up -d`（重建容器生效）。
3. 之后要再加人：需先按第 2 节设 `ADMIN_TOKEN`，进 `https://vault.dawnop.com/admin` 邀请。

## 7. 客户端

- 浏览器插件 / 手机 App（iOS/Android 官方 Bitwarden）→ 设置里把**服务器地址**改成
  `https://vault.dawnop.com`，再登录。
- 桌面 App 同理（Settings → Self-hosted）。

## 8. 备份

**所有状态都在 `/opt/vaultwarden/data/`**（`db.sqlite3` + 附件 + rsa keys）。定期打包：

```bash
tar czf ~/vw-backup-$(date +%F).tgz -C /opt/vaultwarden data
```

密码库整个身家就这一个目录，务必纳入你现有的备份节奏。

## 升级

先按第 3 节用代理拉新版本镜像并打回官方 tag，再改 `docker-compose.yml` 的 image tag →
`sudo docker compose up -d`。SQLite 会自动迁移；升级前先备份 `data/`。

## 运维速查

```bash
cd /opt/vaultwarden
sudo docker compose ps                 # 状态
sudo docker compose logs --tail=100    # 日志
sudo docker compose restart            # 重启
sudo docker compose down               # 停（数据留在 data/）
sudo docker stats vaultwarden --no-stream   # 内存占用（通常几十 MB）
```
