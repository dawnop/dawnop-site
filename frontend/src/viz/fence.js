// 给一个 markdown-it 实例注册 ```viz 围栏：把它渲染成占位 <div class="viz-island" data-viz="id">，
// 渲染后再由 island 管理器挂载真正的交互组件。保持 html:false（不放开任意 HTML），
// 只接受 [a-z0-9-] 白名单标识，安全可控。文章页用。
export function registerVizFence(md) {
  const defaultFence = md.renderer.rules.fence.bind(md.renderer.rules)
  md.renderer.rules.fence = (tokens, idx, options, env, self) => {
    const token = tokens[idx]
    if (token.info.trim().split(/\s+/)[0] === 'viz') {
      const id = token.content.trim().split(/\s+/)[0]
      if (/^[a-z0-9-]+$/.test(id)) {
        return `<div class="viz-island" data-viz="${id}"></div>\n`
      }
      return `<div class="viz-island viz-island--error">非法可视化标识：${md.utils.escapeHtml(id)}</div>\n`
    }
    return defaultFence(tokens, idx, options, env, self)
  }
}
