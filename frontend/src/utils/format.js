// 通用格式化工具：跨组件复用，替代各处重复的 fmtDate / fmtSize / fmtBytes。

// 字节数 → 人类可读（1024 进制）。0 → "0 B"。
export function fmtBytes(n) {
  n = Number(n) || 0
  if (n < 1024) return n + ' B'
  const u = ['KB', 'MB', 'GB', 'TB']
  let i = -1
  do {
    n /= 1024
    i++
  } while (n >= 1024 && i < u.length - 1)
  return n.toFixed(n >= 100 || i === 0 ? 0 : n >= 10 ? 1 : 2) + ' ' + u[i]
}

// ISO 字符串 / 时间戳 → 本地日期（zh-CN，如 2026/7/5）。
export function fmtDate(s) {
  return new Date(s).toLocaleDateString('zh-CN')
}

// 时间戳（ms）→ 简短「X月Y日」，空值 → "—"。文件列表/预览用。
export function fmtMonthDay(ms) {
  if (!ms) return '—'
  const d = new Date(ms)
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

// 秒数 → 「N 天 N 小时 / N 小时 N 分 / N 分」。
export function fmtDuration(sec) {
  sec = Number(sec) || 0
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (d) return `${d} 天 ${h} 小时`
  if (h) return `${h} 小时 ${m} 分`
  return `${m} 分`
}
