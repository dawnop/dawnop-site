#!/usr/bin/env bash
# 应急回滚：把 dawnop.com 从 Dawn 后端（:8001）切回原来的 FastAPI/uvicorn 后端（:8000）。
#
# 背景：M6 的 strangler-fig 迁移收口之后，每个 /api 端点与 dav.dawnop.com 的 WebDAV 都由
# :8001 上的 Dawn 后端提供，FastAPI 服务（dawnop-backend，uvicorn :8000）已经退役
# （`systemctl disable --now dawnop-backend`）。FastAPI 的代码、venv 和库访问都还原封不动
# 留在 /opt/dawnop/backend，所以一次完整回滚就是：
#   1. 把 uvicorn 拉回来（受守护地拉：文件路由关掉、库身份核定并闩死），
#   2. 核验它确实是我们以为的那个进程、连的是我们以为的那个库，
#   3. 才把 nginx 的 /api 与 dav 指回 :8000。
# Dawn 服务（:8001）全程不动，所以回切就是反操作（return-to-dawn.sh）。
#
# 以 root 在生产机上跑：
#   ssh <user>@<server> 'sudo bash -s' < backend-dawn/deploy/rollback-to-fastapi.sh
set -euo pipefail

# 解析 nginx 真正读的那个文件，而不是假设某种布局——这里每一个写死的答案都至少错过一次：
#
#   - sites-enabled/dawnop，直接改：今天是对的（它是普通文件），但任何人一旦按文档恢复
#     软链布局就错了，因为 GNU `sed -i` 不跟随符号链接——它写临时文件再 rename 覆盖过去，
#     于是软链被替换成普通文件，真正的配置一个字没动。
#   - sites-available/dawnop：这个脚本到 2026-07-17 为止就是这么说的，依据是部署文档声称
#     有软链。生产根本没有软链：sites-enabled/dawnop 是普通文件，sites-available/dawnop
#     是没人读的陈旧副本（nginx 只 include sites-enabled/*）。于是那个「修复」把回滚指向了
#     一个毫无效果的文件——一次印着成功、什么都没改的回滚，正是它本该防住的失败。
#
# `readlink -f` 对两种布局都答得对：普通文件解析成自己，软链解析成目标。改解析后的路径
# 永远是真配置，也永远吃不掉软链。
ENABLED=/etc/nginx/sites-enabled/dawnop
CONF=""
STAMP=$(date +%s)
BACKUP=""

# >>> shared FastAPI verification block >>>
# 这一段在 rollback-to-fastapi.sh 与 return-to-dawn.sh 里**逐字节相同**，
# backend/tests/test_rollback_chain.py::test_shared_fastapi_verification_block_is_identical
# 会把两份取出来做逐字节比对，差一个空格就红。
#
# 为什么值得付这个复制的代价：回滚和回切验的必须是**同一个 FastAPI**。这两个脚本一个
# 「切到 FastAPI」、一个「在 Dawn 起不来时留在 FastAPI」，如果各自带一份对单元名、argv、
# 工作目录、运行身份的理解，两边就会慢慢漂开——而漂开的那一天，「回滚成功」和「回切成功」
# 说的是两个不同的进程，谁也不会发现，因为两个脚本都印了成功。常量与判词都放进这一段，
# 于是对齐不是靠人记，是靠比对器。
#
# 这里**没有**任何「已经验过一次」的缓存变量。曾经有过一个 FASTAPI_VERIFIED，让一次
# 核验的结论跨命令复用；它被整个删掉了。保留 FastAPI 和恢复 FastAPI 都是重新完整跑一遍
# verify_guarded_fastapi_runtime。理由很简单：陈旧的结论和没有结论长得一样，而回滚链上
# 每一次「保留 FastAPI」的决定，都发生在世界刚刚变过之后。

FASTAPI_UNIT="dawnop-backend"
FASTAPI_BASE="http://127.0.0.1:8000"
FASTAPI_WORKDIR="/opt/dawnop/backend"
FASTAPI_USER="dawn"
FASTAPI_GROUP="dawn"
# /proc/PID/exe 解析后要落在这里。venv 的 python3 本身是个符号链接，所以两边都 readlink -f
# 之后再比：比的是「跑的是不是同一个二进制」，不是「路径字符串长得像不像」。
FASTAPI_PINNED_INTERPRETER="/opt/dawnop/backend/.venv/bin/python3"
FASTAPI_ARGV="/opt/dawnop/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000"
FASTAPI_PROBE_PATH="/api/rollback/db-identity"
FASTAPI_PROBE_HEADER_NAME="X-Rollback-Probe"
FASTAPI_PROBE_SECRET_FILE="/opt/dawnop/backend/.rollback-probe"
FILE_ROUTE_SENTINEL="/etc/dawnop/fastapi-file-routes-disabled"
DAWNOP_DB_FILE="/opt/dawnop/data/dawnop.db"
SITE_ORIGIN="https://dawnop.com"
DAV_ORIGIN="https://dav.dawnop.com"

fatal() {
  echo "!!! $*" >&2
  exit 1
}

require_root() {
  # 不用 "$0"：这两个脚本的正规调用方式是把文件从本地 pipe 过 ssh，$0 就是 "bash"，
  # 照着 $0 印出来的提示会让人去找一个叫 bash 的文件。
  [ "$(id -u)" -eq 0 ] || fatal "必须以 root 运行：ssh <user>@<server> 'sudo bash -s' < <本脚本>"
}

resolve_path() {
  readlink -f -- "$1" 2>/dev/null || true
}

# ---- 谓词 vs 致命错误：分开 ----
# local_status_is / https_status_is 只回答真假，调用方决定这次要不要死。
# expect_https_status 是「不满足即致命」。混成一个函数的后果是「检查一下」会顺带把脚本
# 退掉：切流后的复验里，有些状态码是用来分支的（例如受守护时 /api/fm 期望 503 而不是 200），
# 一个自带 exit 的检查会让这种分支写不出来，于是分支就不写了。
local_status_is() {
  local base=$1 method=$2 path=$3 want=$4 got
  got=$(curl -s -o /dev/null -w '%{http_code}' -X "$method" --max-time 5 "$base$path" 2>/dev/null || true)
  [ "$got" = "$want" ]
}

https_status_is() {
  local origin=$1 method=$2 path=$3 want=$4 got
  got=$(curl -s -o /dev/null -w '%{http_code}' -X "$method" --max-time 10 "$origin$path" 2>/dev/null || true)
  [ "$got" = "$want" ]
}

expect_https_status() {
  local origin=$1 method=$2 path=$3 want=$4
  https_status_is "$origin" "$method" "$path" "$want" ||
    fatal "$method $origin$path 期望 $want，实测不是"
}

wait_for_local_health() {
  local base=$1 tries=$2 _
  for _ in $(seq 1 "$tries"); do
    if local_status_is "$base" GET /api/health 200; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

unit_main_pid() {
  local pid
  pid=$(systemctl show -p MainPID --value "$1" 2>/dev/null || true)
  [ -n "$pid" ] && [ "$pid" != "0" ] || return 1
  printf '%s' "$pid"
}

# ---- 加载器漂移 ----
# 从 NUL 或空格分隔的 KEY=VALUE 流里挑出第一个会改变加载行为的变量名。
# PYTHONPATH / PYTHONHOME / LD_PRELOAD / LD_LIBRARY_PATH 这类变量能让一个字节不差的
# 可执行文件加载完全不同的代码，所以「跑的是钉住的解释器」并不等于「跑的是钉住的代码」。
# 前缀匹配是刻意宽的：把 PYTHONUNBUFFERED 这种无害的也拦下来，代价是回滚时可能要显式
# 放行一两个名字，收益是不必维护一张「哪些 PYTHON* 是安全的」的名单——那张名单会过期。
#
# ---- 逃生阀 ROLLBACK_ALLOW_ENV ----
# 空格分隔的变量名列表，列进去的名字即使命中上面的前缀也放行。**这是应急出口，不是配置项**：
# 正常回滚不该设它（这个单元没有任何 Environment=，只有一个 EnvironmentFile=，用法见
# deploy/README.md）。它存在的理由只有一个——宽匹配 + fail-closed 卡在应急通道上而又没有
# 出口时，压力下的人会把整个检查注释掉，那才是真正的失效模式。这个阀把「静默被挡」换成
# 「有意识地接受并留痕」：每放行一个名字都往日志里印出那个名字。
ROLLBACK_ALLOW_ENV="${ROLLBACK_ALLOW_ENV:-}"

# 匹配的是**完整变量名相等**，不是前缀、不是子串、也不是 glob：
# ROLLBACK_ALLOW_ENV="PYTHONUNBUFFERED" 放不掉 PYTHONPATH，也放不掉 PYTHONUNBUFFERED_EXTRA。
loader_environment_is_allowed() {
  local name=$1 entry
  local -a allowed
  read -r -a allowed <<< "$ROLLBACK_ALLOW_ENV"
  for entry in "${allowed[@]}"; do
    if [ "$entry" = "$name" ]; then
      return 0
    fi
  done
  return 1
}

loader_environment_offender() {
  # set2 只给一个 \n：tr 会用它补齐 set1 的长度，NUL 与空格都变成换行。
  local names name
  names=$(tr '\0 ' '\n' | awk -F= '$1 ~ /^(LD_|PYTHON)/ { print $1 }')
  # awk 这里**不**能自己 exit。有了白名单之后，要找的是第一个「没被放行的」变量，
  # 而不是第一个命中前缀的变量——第一个恰好被放行，不代表它后面那个也该放行。
  # （顺带修掉一个潜在的坑：awk 提前 exit 会让 tr 吃 SIGPIPE，pipefail 下整条管道非 0。）
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    if loader_environment_is_allowed "$name"; then
      echo "    [escape] ROLLBACK_ALLOW_ENV 放行了加载器环境变量 $name" >&2
      continue
    fi
    printf '%s\n' "$name"
    return 0
  done <<< "$names"
}

verify_unit_loader_environment() {
  local unit=$1 offender
  offender=$(systemctl show -p Environment --value "$unit" 2>/dev/null | loader_environment_offender)
  if [ -n "$offender" ]; then
    echo "!!! 单元 $unit 的 Environment= 里有 $offender，会改变加载行为" >&2
    return 1
  fi
}

verify_process_loader_environment() {
  local pid=$1 offender
  offender=$(loader_environment_offender < "/proc/$pid/environ")
  if [ -n "$offender" ]; then
    echo "!!! PID $pid 的进程环境里有 $offender，会改变加载行为" >&2
    return 1
  fi
}

verify_pinned_interpreter() {
  local pid=$1 pinned_path=$2 pinned actual
  pinned=$(resolve_path "$pinned_path")
  [ -n "$pinned" ] || {
    echo "!!! 钉住的解释器 $pinned_path 解析不到" >&2
    return 1
  }
  actual=$(resolve_path "/proc/$pid/exe")
  [ -n "$actual" ] || {
    echo "!!! 读不到 /proc/$pid/exe" >&2
    return 1
  }
  if [ "$actual" != "$pinned" ]; then
    echo "!!! 解释器漂移：PID $pid 跑的是 $actual，钉住的是 $pinned" >&2
    return 1
  fi
}

verify_unit_identity() {
  local unit=$1 pid=$2 want_argv=$3 want_cwd=$4 want_user=$5 want_group=$6
  local cgroup argv cwd want_cwd_real uid gid want_uid want_gid

  # MainPID 是 systemd 报的，不是证据。先确认这个 PID 真的活在这个单元的 cgroup 里，
  # 否则后面所有针对 /proc/PID 的核验，验的可能是一个跟这个单元无关的进程。
  cgroup=$(cat "/proc/$pid/cgroup" 2>/dev/null || true)
  case "$cgroup" in
    *"$unit.service"*) ;;
    *)
      echo "!!! PID $pid 不在 $unit.service 的 cgroup 里（cgroup=$cgroup）" >&2
      return 1
      ;;
  esac

  argv=$(tr '\0' ' ' < "/proc/$pid/cmdline" || true)
  argv=${argv% }
  if [ "$argv" != "$want_argv" ]; then
    echo "!!! $unit 的 argv 不符" >&2
    echo "    实测：$argv" >&2
    echo "    期望：$want_argv" >&2
    return 1
  fi

  cwd=$(resolve_path "/proc/$pid/cwd")
  want_cwd_real=$(resolve_path "$want_cwd")
  if [ "$cwd" != "$want_cwd_real" ]; then
    echo "!!! $unit 的工作目录不符：实测 $cwd，期望 $want_cwd_real" >&2
    return 1
  fi

  # 第 3 列是**有效** UID/GID。真实 UID 相同而有效 UID 不同的进程，能做的事完全不一样，
  # 而这两个脚本关心的恰恰是「它能不能写那个库」。
  uid=$(awk '/^Uid:/ { print $3 }' "/proc/$pid/status" 2>/dev/null || true)
  gid=$(awk '/^Gid:/ { print $3 }' "/proc/$pid/status" 2>/dev/null || true)
  want_uid=$(id -u "$want_user" 2>/dev/null || true)
  want_gid=$(getent group "$want_group" 2>/dev/null | cut -d: -f3 || true)
  if [ -z "$uid" ] || [ -z "$gid" ]; then
    echo "!!! 读不到 /proc/$pid/status 的有效身份（进程已经没了？）" >&2
    return 1
  fi
  if [ -z "$want_uid" ] || [ -z "$want_gid" ]; then
    echo "!!! 解析不出 $want_user:$want_group 的 uid/gid" >&2
    return 1
  fi
  if [ "$uid" != "$want_uid" ] || [ "$gid" != "$want_gid" ]; then
    echo "!!! $unit 的有效身份不符：实测 $uid:$gid，期望 $want_uid:$want_gid" >&2
    return 1
  fi
}

# ---- 库身份 ----
# 配方与 backend/app/core/db_identity.py 的 fingerprint_of_stat 逐字节相同，
# 由 tests/test_rollback_chain.py::test_fingerprint_matches_stat_and_sha256sum 钉住。
# $(...) 吃掉 stat 的尾换行是**必须的**：少了它 sha256sum 会把那个 \n 一起算进去，
# 两边永远对不上，而对不上的表现是「回滚脚本总说库不对」，没人会怀疑是换行。
expected_database_fingerprint() {
  printf '%s' "$(stat -Lc '%d:%i' -- "$DAWNOP_DB_FILE")" | sha256sum | awk '{ print $1 }'
}

fastapi_probe_json() {
  local secret
  secret=$(tr -d ' \t\n\r' < "$FASTAPI_PROBE_SECRET_FILE")
  curl -sf --max-time 10 -H "$FASTAPI_PROBE_HEADER_NAME: $secret" \
    "$FASTAPI_BASE$FASTAPI_PROBE_PATH"
}

verify_fastapi_database_identity() {
  local want json got latched
  [ -r "$FASTAPI_PROBE_SECRET_FILE" ] || {
    echo "!!! 读不到探针秘密 $FASTAPI_PROBE_SECRET_FILE（要与 .env 的 ROLLBACK_PROBE_HEADER 一致）" >&2
    return 1
  }
  [ -f "$DAWNOP_DB_FILE" ] || {
    echo "!!! 库文件 $DAWNOP_DB_FILE 不存在" >&2
    return 1
  }
  want=$(expected_database_fingerprint)
  json=$(fastapi_probe_json) || {
    echo "!!! 探针 $FASTAPI_BASE$FASTAPI_PROBE_PATH 没有答复（未闩会回 503，头不对会回 404）" >&2
    return 1
  }
  got=$(printf '%s' "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["database_fingerprint"])')
  latched=$(printf '%s' "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["process_guard_latched"])')
  if [ "$latched" != "True" ]; then
    echo "!!! 进程守卫没有闩上：$FILE_ROUTE_SENTINEL 是在进程起来之后才放的？" >&2
    return 1
  fi
  if [ "$got" != "$want" ]; then
    echo "!!! 库身份对不上：进程闩住的是 $got，$DAWNOP_DB_FILE 是 $want" >&2
    echo "    最可能的原因：$FASTAPI_WORKDIR/.env 的 DATABASE_URL 还是 M6 之前的相对路径。" >&2
    echo "    见 deploy/README.md 的「DATABASE_URL 必须写绝对路径」。" >&2
    return 1
  fi
}

# ---- 驱动：受守护的 FastAPI 运行时，完整核验一遍 ----
# 调用点没有前置条件，也不会因为「刚刚验过」而跳过任何一步。
verify_guarded_fastapi_runtime() {
  local pid
  echo "    [verify] 完整核验 FastAPI 运行时（无缓存结论）"

  [ -e "$FILE_ROUTE_SENTINEL" ] || fatal "哨兵 $FILE_ROUTE_SENTINEL 不在，这不是一次受守护的回滚"
  wait_for_local_health "$FASTAPI_BASE" 40 || fatal "$FASTAPI_BASE/api/health 没有变健康"

  pid=$(unit_main_pid "$FASTAPI_UNIT") || fatal "$FASTAPI_UNIT 没有 MainPID（服务没起来？）"
  echo "    [verify] $FASTAPI_UNIT MainPID=$pid"

  verify_unit_identity "$FASTAPI_UNIT" "$pid" \
    "$FASTAPI_ARGV" "$FASTAPI_WORKDIR" "$FASTAPI_USER" "$FASTAPI_GROUP" ||
    fatal "$FASTAPI_UNIT 的单元/argv/cwd/身份核验没过"
  verify_pinned_interpreter "$pid" "$FASTAPI_PINNED_INTERPRETER" ||
    fatal "$FASTAPI_UNIT 跑的不是钉住的解释器"

  # 两个加载器环境检查缺一不可。单元定义检查有盲区：EnvironmentFile= 指向的内容
  # `systemctl show -p Environment` 是看不见的（这个单元恰好就有一个 EnvironmentFile=
  # 指向 .env），只有进程环境检查能兜住。反过来，进程环境检查也不能单独立——它要求进程
  # 已经在跑，而单元定义检查在读的是「下一次起来会是什么样」。
  verify_unit_loader_environment "$FASTAPI_UNIT" ||
    fatal "$FASTAPI_UNIT 的单元定义里有加载器环境变量"
  verify_process_loader_environment "$pid" ||
    fatal "$FASTAPI_UNIT 的进程环境里有加载器环境变量"

  verify_fastapi_database_identity || fatal "FastAPI 连的库不是 $DAWNOP_DB_FILE"

  # 受守护 = 文件路由必须是关的。这条是守卫真的在守的机器证据，不是靠哨兵文件在不在推断。
  local_status_is "$FASTAPI_BASE" GET "/api/fm?path=qiniu://" 503 ||
    fatal "受守护的 FastAPI 仍在提供 /api/fm（期望 503）"

  echo "    [verify] 通过：单元/argv/cwd/身份、解释器、加载器环境（单元+进程）、库身份、文件路由已关"
}
# <<< shared FastAPI verification block <<<

require_root

echo "==> 1/5 前置检查与守护开关"
[ -e "$ENABLED" ] || fatal "$ENABLED 不存在——这台机器对吗？"
CONF=$(readlink -f "$ENABLED")
[ -f "$CONF" ] || fatal "$CONF 不是普通文件"
BACKUP="/etc/nginx/backups/dawnop.pre-rollback.$STAMP"
echo "    nginx 读的是：$ENABLED -> $CONF"

# 哨兵**必须在进程起来之前**放好：守卫是进程级闩存，起来之后才放的哨兵这一代进程看不见
# （见 backend/app/core/process_guard.py）。下一步用 restart 而不是 `enable --now`，就是
# 为了保证读到哨兵的是一个全新的进程——`enable --now` 对一个已经在跑的服务什么都不做。
mkdir -p "$(dirname "$FILE_ROUTE_SENTINEL")"
: > "$FILE_ROUTE_SENTINEL"
echo "    哨兵已放置：$FILE_ROUTE_SENTINEL（本次回滚是受守护的：/api/fm 关闭）"

echo "==> 2/5 受守护地拉起 FastAPI（uvicorn :8000）"
systemctl enable "$FASTAPI_UNIT"
systemctl restart "$FASTAPI_UNIT"

echo "==> 3/5 核验 FastAPI 运行时"
# **顺序是有意的：核验在动 nginx 之前。**
#
# 单元定义的加载器环境检查有一个盲区——`systemctl show -p Environment` 看不见
# EnvironmentFile= 指向的文件内容，而这个单元恰好有一个 EnvironmentFile=/opt/dawnop/backend/.env。
# 补上盲区的是进程环境检查（/proc/PID/environ），但它要求进程已经在跑。
#
# 于是「进程已经起来」和「流量还没切过去」之间的这个窗口，是唯一能既看得见真实进程环境、
# 又还来得及不切流的时刻。把核验挪到第 4 步之后，盲区就没人兜了：等到发现 .env 里有一行
# PYTHONPATH，公网流量已经在那个进程上了。
verify_guarded_fastapi_runtime

echo "==> 4/5 把 nginx 的 /api 与 dav.dawnop.com 指回 :8000"
mkdir -p /etc/nginx/backups
cp -a "$CONF" "$BACKUP"

# 每一处替换都先核对而不是假设。sed 匹配不到任何东西也退出 0，所以一个没核对的 sed 会把
# 回滚变成一次仍然印着成功的空操作——`client_max_body_size 64m` 那行就是这么在这里活过
# nginx.conf 改成 `0` 之后很久的。
sub() {
  local pat=$1 desc=$2
  grep -qF -- "$pat" "$CONF" || {
    echo "    动手改之前请自己看一眼；备份在 $BACKUP" >&2
    fatal "$CONF 里没有 '$pat'（$desc）——配置漂了，不动 nginx"
  }
}
sub 'proxy_pass http://127.0.0.1:8001;' '/api/ upstream'
sub 'proxy_pass http://127.0.0.1:8001/dav/;' 'dav vhost upstream'
sed -i 's#proxy_pass http://127.0.0.1:8001;#proxy_pass http://127.0.0.1:8000;#' "$CONF"
sed -i 's#proxy_pass http://127.0.0.1:8001/dav/;#proxy_pass http://127.0.0.1:8000/dav/;#' "$CONF"
# client_max_body_size 不用动：dav vhost 已经是 `0`（不限），两个后端都把 PUT 的 body
# 流到磁盘，这个上限与后端无关。

# `nginx -t` 失败时如果就这么被 set -e 退掉，磁盘上会留着一份坏配置：nginx 还在用旧的，
# 所以一切看起来都正常，直到某个毫不相干的 reload 或一次重启失败——那时站点挂掉的原因，
# 和触发它的人没有任何关系。
if ! nginx -t; then
  cp -a "$BACKUP" "$CONF"
  fatal "nginx -t 没过——已还原 $BACKUP，nginx 保持原样"
fi
systemctl reload nginx

echo "==> 5/5 切流后复验"
expect_https_status "$SITE_ORIGIN" GET /api/health 200
expect_https_status "$SITE_ORIGIN" GET /api/pages/nav 200
expect_https_status "$DAV_ORIGIN" OPTIONS / 401
# 受守护 = 文件路由是关的，公网也必须这么表现（401 会说明守卫根本没生效）。
https_status_is "$SITE_ORIGIN" GET '/api/fm?path=qiniu://' 503 ||
  fatal "公网 /api/fm 不是 503——守卫没有生效"

echo
echo "已回滚到 FastAPI :8000。Dawn :8001 仍在运行（未动）。"
echo "  nginx 备份：$BACKUP"
echo "  哨兵：$FILE_ROUTE_SENTINEL（回切时由 return-to-dawn.sh 移除）"
echo "  回切（服务器上没有这两个脚本的副本，要从本地仓库 pipe 过去跑）："
echo "    ssh <user>@<server> 'sudo bash -s' < backend-dawn/deploy/return-to-dawn.sh"
