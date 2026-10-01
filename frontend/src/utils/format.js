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

// 时间值归一为毫秒：unix 秒（< 1e12）、毫秒、ISO 字符串（日期与时间之间是空格也行）都接受；
// 解析不了返回 NaN。上传链接的 expires_at 是 unix 秒、created_at 是字符串，两种都走这里。
export function toMs(v) {
  if (v === null || v === undefined || v === '') return NaN
  if (typeof v === 'number') return v < 1e12 ? v * 1000 : v
  if (/^\d+$/.test(String(v))) return toMs(Number(v))
  return Date.parse(String(v).replace(' ', 'T'))
}

// 时间值 → 本地「2026/10/1 14:05」，空值或解析失败 → "—"。
export function fmtDateTime(v) {
  const ms = toMs(v)
  if (!Number.isFinite(ms)) return '—'
  return new Date(ms).toLocaleString('zh-CN', {
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

// 时间戳（ms）→ 简短「X月Y日」，空值 → "—"。文件列表/预览用。
export function fmtMonthDay(ms) {
  if (!ms) return '—'
  const d = new Date(ms)
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

// 字数 → 阅读时长（分钟）：按 300 字/分钟，不足一分钟按一分钟算。
export function fmtReadMinutes(wc) {
  return Math.max(1, Math.round((wc || 0) / 300))
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
