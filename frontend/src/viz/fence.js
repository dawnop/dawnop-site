// 给一个 markdown-it 实例注册 ```viz 围栏：把它渲染成占位 <div class="viz-island" data-viz="id">，
// 渲染后再由 island 管理器挂载真正的交互组件。保持 html:false（不放开任意 HTML），
// 只接受 [a-z0-9-] 白名单标识，安全可控。文章页与编辑器预览共用。
//
// 参数传递：围栏体首行是标识，其后可跟一段 JSON 对象，作为组件 props 传入——
// 让「文字/数据」写在 markdown 里，组件只负责呈现（见 about-timeline 等）。例：
//   ```viz
//   about-timeline
//   { "closing": "…", "nodes": [ … ] }
//   ```
// JSON 校验、归一后原样塞进 data-viz-props（转义），挂载时（islands.js）解析成 props。
// 无参数时行为不变（仅 data-viz），对已有引用完全向后兼容。
export function registerVizFence(md) {
  const defaultFence = md.renderer.rules.fence.bind(md.renderer.rules)
  md.renderer.rules.fence = (tokens, idx, options, env, self) => {
    const token = tokens[idx]
    if (token.info.trim().split(/\s+/)[0] !== 'viz') {
      return defaultFence(tokens, idx, options, env, self)
    }
    const body = token.content.trim()
    // 首个 [a-z0-9-] 段是标识，其余（可跨行）是参数体。
    const m = body.match(/^([a-z0-9-]+)\s*([\s\S]*)$/)
    if (!m) {
      const bad = body.split(/\s+/)[0] || ''
      return `<div class="viz-island viz-island--error">非法可视化标识：${md.utils.escapeHtml(bad)}</div>\n`
    }
    const id = m[1]
    const paramText = m[2].trim()
    if (!paramText) {
      return `<div class="viz-island" data-viz="${id}"></div>\n`
    }
    let props
    try {
      props = JSON.parse(paramText)
    } catch (e) {
      return `<div class="viz-island viz-island--error">可视化「${md.utils.escapeHtml(id)}」参数不是合法 JSON</div>\n`
    }
    if (props === null || typeof props !== 'object' || Array.isArray(props)) {
      return `<div class="viz-island viz-island--error">可视化「${md.utils.escapeHtml(id)}」参数必须是 JSON 对象</div>\n`
    }
    const attr = md.utils.escapeHtml(JSON.stringify(props))
    return `<div class="viz-island" data-viz="${id}" data-viz-props="${attr}"></div>\n`
  }
}
