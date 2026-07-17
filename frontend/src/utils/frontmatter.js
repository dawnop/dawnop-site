// 解析 markdown 顶部的 YAML front matter（导入 .md 时提取 title/summary/slug/published 等元数据）。
// 与后端 export 的 _build_frontmatter 对称。仅认文件最开头的 `---` 块，正文中的 `---` 不受影响。
// 用一个极简 YAML 子集（key: value 标量、"..."/'...' 引号、true/false、[a, b] 简单数组），零依赖。

function parseValue(raw) {
  const v = raw.trim()
  if (v === '') return ''
  if ((v[0] === '"' && v.endsWith('"')) || (v[0] === "'" && v.endsWith("'"))) {
    const inner = v.slice(1, -1)
    return v[0] === '"' ? inner.replace(/\\"/g, '"').replace(/\\\\/g, '\\') : inner
  }
  if (v[0] === '[' && v.endsWith(']')) {
    return v
      .slice(1, -1)
      .split(',')
      .map((x) => parseValue(x))
      .filter((x) => x !== '')
  }
  if (v === 'true') return true
  if (v === 'false') return false
  return v
}

// 返回 { meta, body }。无合法 front matter（不以 --- 开头、或未闭合）时 meta={}、body 为原文。
export function parseFrontmatter(md) {
  if (!md) return { meta: {}, body: md || '' }
  const src = md.replace(/^\uFEFF/, '') // 去 BOM（写转义而非字面 BOM：后者在编辑器里是隐形的）
  const lines = src.split('\n')
  if (lines[0].trim() !== '---') return { meta: {}, body: md }
  let end = -1
  for (let i = 1; i < lines.length; i++) {
    if (lines[i].trim() === '---') { end = i; break }
  }
  if (end === -1) return { meta: {}, body: md } // 未闭合 → 当作正文，不误剥
  const meta = {}
  for (let i = 1; i < end; i++) {
    const line = lines[i]
    if (!line.trim() || line.trim().startsWith('#')) continue
    const m = line.match(/^([A-Za-z_][\w-]*)\s*:\s*(.*)$/)
    if (m) meta[m[1]] = parseValue(m[2])
  }
  const body = lines.slice(end + 1).join('\n').replace(/^\n+/, '')
  return { meta, body }
}
