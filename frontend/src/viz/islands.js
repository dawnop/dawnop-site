// 「岛屿」挂载管理器：把容器内所有 .viz-island[data-viz] 占位挂载成真正的交互组件。
// 与 Red Blob Games / Distill 同款的「占位 + 按标识挂载」机制。
// 文章页与文章编辑器预览共用此管理器（各持一个实例）。
import { createApp } from 'vue'
import { resolveViz } from './registry'
import { acquireStyle } from './runtime'
import './island.css'

export function createIslandManager() {
  let mounted = [] // { app, release, node }

  function unmountAll() {
    mounted.forEach(({ app, release, node }) => {
      try {
        app.unmount()
      } catch (e) {
        /* 忽略 */
      }
      try {
        release()
      } catch (e) {
        /* 忽略 */
      }
      // 清掉去重标记：md-editor 预览重渲染可能复用同一节点（保留 data-mounted），
      // 卸载后必须清标记，否则下次 mount 会因「已挂载」而跳过这个被清空的节点。
      if (node && node.isConnected) delete node.dataset.mounted
    })
    mounted = []
  }

  // 可重复调用：只挂未挂的占位（data-mounted 去重，防同一轮重复挂载）。
  async function mount(rootEl) {
    if (!rootEl) return
    for (const node of rootEl.querySelectorAll('.viz-island[data-viz]')) {
      if (node.dataset.mounted) continue
      node.dataset.mounted = '1'
      const id = node.dataset.viz
      try {
        const { component, style } = await resolveViz(id)
        // 异步解析期间节点可能被预览重渲染替换掉，此时不挂（留给下一轮）
        if (!node.isConnected) {
          delete node.dataset.mounted
          continue
        }
        const release = acquireStyle(id, style)
        const app = createApp(component)
        app.mount(node)
        mounted.push({ app, release, node })
      } catch (e) {
        node.classList.add('viz-island--error')
        node.textContent = `可视化「${id}」加载失败`
      }
    }
  }

  return { mount, unmountAll }
}
