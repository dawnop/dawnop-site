// 监控页专用的小格式化 / 派生函数（跨监控卡片复用）。
// 通用的 fmtBytes / fmtDuration 在 utils/format.js。

// ISO → 距今天数（向上取整），空值 → null
export function daysUntil(iso) {
  if (!iso) return null
  return Math.ceil((new Date(iso) - Date.now()) / 86400000)
}

// ISO → 「M/D」，空值 → "—"
export function shortDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

// ISO → 「YYYY-MM-DD」，空值 → ""
export function dateYmd(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

// used/total → 百分比（一位小数，封顶 100）
export function pct(used, total) {
  total = Number(total) || 0
  if (!total) return 0
  return Math.min(100, Math.round((Number(used) / total) * 1000) / 10)
}

// 百分比 → 进度条颜色（≥90 红 / ≥70 橙 / 其余蓝）
export function barColor(p) {
  return p >= 90 ? '#cf1322' : p >= 70 ? '#d48806' : '#1677ff'
}

// unix 秒 → 「M/D」（趋势图 x 轴标签）
export function labelFromUnix(t) {
  const d = new Date(t * 1000)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

// bps → 「x.xx Mbps」
export function fmtMbps(bps) {
  return ((Number(bps) || 0) / 1e6).toFixed(2) + ' Mbps'
}
