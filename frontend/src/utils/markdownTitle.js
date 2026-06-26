// Markdown 标题工具：取正文第一个 H1 作标题、以及渲染时剥离这行 H1（避免与单独渲染的标题重复）。
// 二者都跳过代码围栏内的 # 注释，只认行首单个 `#` + 空格的一级标题。

function isFence(line) {
  const m = line.match(/^\s*(```+|~~~+)/)
  return m ? m[1][0] : null // 返回围栏字符（` 或 ~），非围栏行返回 null
}

// 第一个一级标题文本（无则 ''）
export function firstH1(md) {
  if (!md) return ''
  let fence = null
  for (const line of md.split('\n')) {
    const f = isFence(line)
    if (f) {
      if (!fence) fence = f
      else if (line.trim().startsWith(fence)) fence = null
      continue
    }
    if (fence) continue
    const m = line.match(/^#[ \t]+(.+?)\s*$/)
    if (m) return m[1].trim()
  }
  return ''
}

// 删掉第一个一级标题那行（及紧随的一个空行），用于渲染去重；不改变原始存储内容
export function stripFirstH1(md) {
  if (!md) return md
  const lines = md.split('\n')
  let fence = null
  for (let i = 0; i < lines.length; i++) {
    const f = isFence(lines[i])
    if (f) {
      if (!fence) fence = f
      else if (lines[i].trim().startsWith(fence)) fence = null
      continue
    }
    if (fence) continue
    if (/^#[ \t]+.+/.test(lines[i])) {
      lines.splice(i, 1)
      if (i < lines.length && lines[i].trim() === '') lines.splice(i, 1)
      return lines.join('\n').replace(/^\n+/, '')
    }
  }
  return md
}
