#!/usr/bin/env bash
# 生产 SQLite 库的备份：取快照 → 校验副本 → 压缩 → 轮转旧份。
#
# 由 dawnop-backup.timer 每天拉起一次，也可以手动跑。默认值指向生产路径，
# 三个都能用环境变量或命令行覆盖（测试就靠这个，别把路径写死回去）：
#
#   DB=/tmp/x.db DEST=/tmp/bk KEEP=3 deploy/backup-db.sh
#   deploy/backup-db.sh --db /tmp/x.db --dest /tmp/bk --keep 3
#
# 快照与校验由站点自己的 --backup 维护命令完成，版本门要求 SQLite >= 3.51.3。
# JVM 默认使用正式 jar 与同目录 lib/；S13 验收后可配置 BACKUP_ENGINE=native。
# 不使用系统 SQLite CLI，也不加载后端密钥；扩展路径须显式配置。
# 依赖：JVM 模式需要 Java，native 模式需要已验收的 ELF；两者均需 gzip、sha256sum。
#
# 这个方案只防误删/误改，不防机器丢失：备份和库在同一块盘上。异地副本是待办。

set -euo pipefail
shopt -s nullglob

DB=${DB:-/opt/dawnop/data/dawnop.db}
DEST=${DEST:-/opt/dawnop/backups}
KEEP=${KEEP:-14}
BACKUP_ENGINE=${BACKUP_ENGINE:-jvm}
BACKUP_JAR=${BACKUP_JAR:-/opt/dawnop-dawn/backend-dawn.jar}
BACKUP_NATIVE=${BACKUP_NATIVE:-/opt/dawnop-dawn/native/current/dawnop-native}
JAVA=${JAVA:-${JAVA_HOME:+$JAVA_HOME/bin/}java}

while (($# > 0)); do
  case "$1" in
    --db) DB=${2:?--db 要跟路径}; shift 2 ;;
    --dest) DEST=${2:?--dest 要跟路径}; shift 2 ;;
    --keep) KEEP=${2:?--keep 要跟数字}; shift 2 ;;
    -h | --help)
      sed -n '2,/^$/p' "$0"
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

for tool in gzip sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || die "找不到 $tool，装上再跑"
done

[[ -f $DB ]] || die "源库不存在或不是普通文件：$DB"
[[ -r $DB ]] || die "源库读不了：$DB"

[[ $KEEP =~ ^[0-9]+$ ]] || die "KEEP 必须是非负整数，收到：$KEEP"
# KEEP=0 会把刚写出来的这份也删掉，那不是备份是删除。
((KEEP >= 1)) || die "KEEP 至少是 1，收到：$KEEP"

# 参数各占一个 argv，不使用 eval 或一整串命令配置。
engine=()
case "$BACKUP_ENGINE" in
  jvm)
    command -v "$JAVA" >/dev/null 2>&1 || die "找不到 Java：$JAVA"
    [[ -f $BACKUP_JAR && -r $BACKUP_JAR ]] || die "读不了站点 jar：$BACKUP_JAR"
    engine=("$JAVA" -Xmx256m -jar "$BACKUP_JAR")
    ;;
  native)
    [[ -f $BACKUP_NATIVE && -x $BACKUP_NATIVE ]] || die "不能执行 native：$BACKUP_NATIVE"
    engine=("$BACKUP_NATIVE")
    ;;
  *) die "BACKUP_ENGINE 必须是 jvm 或 native：$BACKUP_ENGINE" ;;
esac

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
cleanup() { rm -f -- "$tmp" "$tmp.gz" "$tmp-wal" "$tmp-shm" "$tmp-journal"; }
trap cleanup EXIT

# 独立维护进程不读业务 .env，不迁移，不改源库日志模式。配置的 tokenizer
# 由同一引擎严格加载；任何快照、副本完整性或 FTS 校验失败都不压缩、不轮转。
DAWNOP_ENV=/dev/null DAWN_DB_PATH="$DB" "${engine[@]}" --backup "$tmp" \
  || die "取快照或校验失败：$DB"

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
