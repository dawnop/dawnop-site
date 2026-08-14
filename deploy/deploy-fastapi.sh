#!/usr/bin/env bash
# 把仓库里的 FastAPI 应用代码推到生产的回滚目标上。
#
# **为什么需要这个脚本。** `backend/` 冻结之后，`deploy.sh` 只部署 Dawn，于是没有任何东西
# 把 FastAPI 代码送上服务器。2026-08-14 查出生产上那份停在 07-06：`#236` 加的
# `app/api/rollback.py`、`app/core/db_identity.py`、`app/core/process_guard.py` 三个文件
# 一个都不在。后果不只是「旧」：
#   * 没有 process_guard，回滚时的哨兵形同虚设，`/api/fm` 照常对着共用的七牛桶开着；
#   * 没有那条 guarded lifespan，进程一起来就 `init_db + ensure_article_fts` 写库；
#   * 没有探针端点，回滚脚本的库身份核验拿不到答案，回滚会 fail-closed 地停下。
# 也就是说：回滚链的正确性依赖被回滚方的代码，而那份代码没有部署路径。这个脚本就是那条路径。
#
# **只同步 app/。** 不是图省事，是边界：`.env`、`.rollback-probe`、`extensions/libsimple.so`、
# `.venv/`、`scripts/` 全都在 `/opt/dawnop/backend/` 下、`app/` 之外，所以 `--delete`
# 在构造上就够不到它们。deploy/README.md 里那条同步整个 backend/ 的 rsync 曾经会用开发机的
# `.env` 覆盖生产密钥（#240 给它补了 --exclude）；把范围收窄到 app/，那类事故不再需要靠
# 「记得写 --exclude」来避免。
#
# 新代码不引入任何新依赖、也不引入新的必填配置（`rollback_probe_header` 有默认值），
# 所以这里不碰 venv、不跑 pip。真要动依赖，那是另一件事，也该另有一个脚本。
#
# 在**开发机的仓库根目录**跑（不是在服务器上）：
#   deploy/deploy-fastapi.sh --dry-run          # 只看会改什么
#   deploy/deploy-fastapi.sh --yes              # 真的推
set -euo pipefail

HOST="${DEPLOY_HOST:-dawn@dawnop.com}"
REMOTE_APP="/opt/dawnop/backend/app"
UNIT="dawnop-backend"

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
LOCAL_APP="$REPO/backend/app"

fatal() {
  echo "!!! $*" >&2
  exit 1
}

dry_run=true
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=true ;;
    --yes) dry_run=false ;;
    *) fatal "未知参数 $arg（只认 --dry-run / --yes）" ;;
  esac
done

[ -d "$LOCAL_APP" ] || fatal "找不到 $LOCAL_APP"

# 推上去的必须是提交过的东西。否则服务器上跑的是一份没有名字的代码，而漂移检查器
# 拿 git 里的内容去比，会把你本地未提交的改动报成「服务器落后」，反过来也一样。
if ! git -C "$REPO" diff --quiet -- backend/app ||
   ! git -C "$REPO" diff --cached --quiet -- backend/app; then
  fatal "backend/app 有未提交的改动。先提交再部署：推上去的应当是被评审过的那份。"
fi

# 回滚进行中就不要在它脚下换代码。uvicorn 已经把模块导进内存了，替换源码既不会生效，
# 又会让「服务器上是什么」和「进程在跑什么」分叉——而那正是这一整套核验想消灭的东西。
if ssh -o BatchMode=yes "$HOST" "systemctl is-active --quiet $UNIT"; then
  fatal "$UNIT 正在运行（回滚进行中？）。先停掉它再部署。"
fi

echo "==> 1/4 同步 $LOCAL_APP/ → $HOST:$REMOTE_APP/"
rsync_flags=(
  -rlptD                       # -a 去掉 -og：属主由 --chown 显式定，不靠名字碰巧对上
  --delete                     # 仓库里删掉的模块，服务器上也要消失
  --exclude=__pycache__/
  --exclude='*.pyc'
  --chown=dawn:dawn            # 与现状一致。要点是**不归 dawnop**：服务改不了自己的代码
  --chmod=D755                 # 拆成两个 --chmod 而不是 `D755,F644`：逗号会让
  --chmod=F644                 # shellcheck 把它读成数组元素分隔符（SC2054）
  --rsync-path="sudo rsync"
  --itemize-changes
)
$dry_run && rsync_flags+=(--dry-run)

rsync "${rsync_flags[@]}" "$LOCAL_APP/" "$HOST:$REMOTE_APP/"

if $dry_run; then
  echo
  echo "以上是 --dry-run。确认无误后加 --yes 真的执行。"
  exit 0
fi

echo
echo "==> 2/4 清掉旧的 __pycache__（rsync 排除了它们，所以不会自己消失）"
ssh -o BatchMode=yes "$HOST" \
  "sudo find $REMOTE_APP -type d -name __pycache__ -prune -exec rm -rf {} +"

echo
echo "==> 3/4 导入自检（语法 + 依赖齐不齐，不起服务）"
# 只 import 应用模块，不进 lifespan，所以不碰库。起不来的话，这里就该看见，
# 而不是等到真的要回滚、`systemctl start` 之后才发现。
ssh -o BatchMode=yes "$HOST" \
  "cd /opt/dawnop/backend && sudo -u dawnop ./.venv/bin/python -c 'import app.main'" ||
  fatal "新代码在服务器上 import 不起来——服务器上的 app/ 现在是坏的，立刻查"

echo
echo "==> 4/4 漂移复验"
python3 "$REPO/scripts/check_server_drift.py" --host "$HOST" ||
  fatal "推完仍有漂移，见上面的清单"

echo
echo "FastAPI 代码已与仓库一致。单元仍是 disabled（回滚目标常态就是停用）。"
