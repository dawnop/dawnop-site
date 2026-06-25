// 在 md-editor 预览区把 ```viz 占位渲染成真实交互组件（类似 mermaid 在编辑器里直接出图）。
//
// md-editor 每次内容变化会整段重渲染预览 DOM（旧节点被替换），故每次都先卸载旧实例再挂新的；
// 用防抖收敛高频变更。返回的 onHtmlChanged 绑到 <MdEditor @on-html-changed>。
import { onBeforeUnmount } from 'vue'
import { createIslandManager } from './islands'

export function useEditorPreviewIslands(previewId) {
  const islands = createIslandManager()
  let timer = null

  function remount() {
    const el = document.getElementById(previewId)
    if (!el) return
    islands.unmountAll()
    islands.mount(el)
  }

  function onHtmlChanged() {
    clearTimeout(timer)
    timer = setTimeout(remount, 200)
  }

  onBeforeUnmount(() => {
    clearTimeout(timer)
    islands.unmountAll()
  })

  return { onHtmlChanged }
}
