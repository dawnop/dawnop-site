#!/usr/bin/env bash
# 生产 SQLite 库的备份：取快照 → 校验副本 → 压缩 → 轮转旧份。
#
# 由 dawnop-backup.timer 每天拉起一次，也可以手动跑。默认值指向生产路径，
# 三个都能用环境变量或命令行覆盖（测试就靠这个，别把路径写死回去）：
#
#   DB=/tmp/x.db DEST=/tmp/bk KEEP=3 deploy/backup-db.sh
#   deploy/backup-db.sh --db /tmp/x.db --dest /tmp/bk --keep 3
#
# 为什么是 `sqlite3 .backup` 而不是 `cp`：后端是活的，随时可能正在写。`cp` 读到的是
# 一个横跨若干次写入的字节混合体，压根不是任何一个时刻的库；WAL 模式下还会漏掉
# 只存在于 -wal 里的已提交事务。`.backup` 走 SQLite 的在线备份 API，拿到的是一个
# 一致的快照，未提交的事务不会出现在里面。实测（delete 与 wal 两种日志模式）：
# 源库开着未提交的写事务时，快照 integrity_check 为 ok 且只含已提交的行。
#
# 依赖：sqlite3、gzip、sha256sum。sqlite3 不是 Ubuntu 的默认包，服务器上要
# `apt-get install sqlite3`（见 deploy/README.md 的「五、库备份」）。
#
# 这个方案只防误删/误改，不防机器丢失：备份和库在同一块盘上。异地副本是待办。

set -euo pipefail
shopt -s nullglob

DB=${DB:-/opt/dawnop/data/dawnop.db}
DEST=${DEST:-/opt/dawnop/backups}
KEEP=${KEEP:-14}

while (($# > 0)); do
  case "$1" in
    --db) DB=${2:?--db 要跟路径}; shift 2 ;;
    --dest) DEST=${2:?--dest 要跟路径}; shift 2 ;;
    --keep) KEEP=${2:?--keep 要跟数字}; shift 2 ;;
    -h | --help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *)
      echo "backup-db: 不认识的参数 $1" >&2
      exit 2
      ;;
  esac
done

die() {
  echo "backup-db: $*" >&2
  exit 1
}

# ---- 前置检查（全在动手之前做完，尤其是 KEEP：它决定删几个）----

for tool in sqlite3 gzip sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || die "找不到 $tool，装上再跑（sqlite3 需 apt-get install sqlite3）"
done

[[ -f $DB ]] || die "源库不存在或不是普通文件：$DB"
[[ -r $DB ]] || die "源库读不了：$DB"

[[ $KEEP =~ ^[0-9]+$ ]] || die "KEEP 必须是非负整数，收到：$KEEP"
# KEEP=0 会把刚写出来的这份也删掉，那不是备份是删除。
((KEEP >= 1)) || die "KEEP 至少是 1，收到：$KEEP"

# 临时文件名要交给 sqlite3 的 `.backup` 点命令，那是一层自己的引号解析。
# 目录名里带单引号会把它解析歪，直接挡掉，不去猜转义规则。
[[ $DEST != *"'"* ]] || die "备份目录路径不能含单引号：$DEST"

mkdir -p -- "$DEST"
# 700/600：备份里有管理员的 bcrypt 口令哈希和全部文章正文（含草稿）。
chmod 700 -- "$DEST"

# ---- 取快照 ----

stamp=$(date -u +%Y%m%dT%H%M%SZ)
name="dawnop-${stamp}.db.gz"
final="$DEST/$name"

# 落在 DEST 里，跟成品同一个文件系统，最后一步的 mv 才是同盘改名（不是复制）。
# 点开头 + 固定前缀，与轮转的 dawnop-*.db.gz 模式不重叠，半成品永远不会被当成备份。
tmp=$(mktemp "$DEST/.backup-db.XXXXXXXX")
cleanup() { rm -f -- "$tmp" "$tmp.gz"; }
trap cleanup EXIT

# 源库坏到打不开时，sqlite3 在这里就退 1（并留下一个 0 字节的目标文件，靠 trap 清掉）。
sqlite3 "$DB" ".backup '$tmp'" || die "取快照失败（源库可能已损坏）：$DB"

# 校验的是**副本**，不是源库。源库能打开不代表页面都是好的，而 .backup 是按页
# 原样搬运，坏页会一起搬过来。实测：改坏第二页的头部，.backup 照样退 0，
# 只有副本上的 integrity_check 会说话。
check=$(sqlite3 "$tmp" 'PRAGMA integrity_check;' 2>&1) || die "快照校验跑不起来：$check"
[[ $check == ok ]] || die "快照没通过 integrity_check，不保留：${check%%$'\n'*}"

gzip -c -- "$tmp" >"$tmp.gz"
chmod 600 -- "$tmp.gz"
mv -- "$tmp.gz" "$final"

(cd "$DEST" && sha256sum -- "$name" >"$name.sha256")
chmod 600 -- "$final.sha256"

size=$(stat -c %s -- "$final")

# ---- 轮转 ----
#
# 这一段是全脚本最危险的地方：它删文件，而且以 root 或 dawnop 的身份删。
# 判据收得死紧——先用固定前缀的 glob 拿候选，再用**完整锚定的正则**逐个复核，
# 只有自己写出来的那个命名模式才会被删。目录里放着的别的东西（README、
# 手动导出的 dump、名字长得像但不匹配的文件）一律留着。
# 排序按文件名，不按 mtime：名字里嵌的是 UTC 时间戳，字典序就是时间序，
# 而 mtime 会被 cp/rsync/touch 改写，不能当权威。

existing=()
for f in "$DEST"/dawnop-*.db.gz; do
  base=${f##*/}
  [[ $base =~ ^dawnop-[0-9]{8}T[0-9]{6}Z\.db\.gz$ ]] || continue
  existing+=("$base")
done

pruned=0
if ((${#existing[@]} > KEEP)); then
  mapfile -t sorted < <(printf '%s\n' "${existing[@]}" | LC_ALL=C sort)
  for base in "${sorted[@]:0:${#sorted[@]} - KEEP}"; do
    rm -f -- "$DEST/$base" "$DEST/$base.sha256"
    pruned=$((pruned + 1))
  done
fi

echo "backup-db: 写入 $final（$size 字节），保留 $KEEP 份，删除旧备份 $pruned 个"
