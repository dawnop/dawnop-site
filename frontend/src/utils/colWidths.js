import { reactive } from 'vue'

// 管理页表格「拖表头调列宽」的本地记忆：
//   const { colW, onHeaderDrag } = useColWidths('dawnop_colw_articles')
//   <el-table @header-dragend="onHeaderDrag"> + 各列 :width="colW['标题'] || 默认宽"
// 列的 key 取 prop（有则）或 label，宽度存 localStorage，下次进页面恢复。
export function useColWidths(storageKey) {
  let saved
  try {
    saved = JSON.parse(localStorage.getItem(storageKey) || '{}')
  } catch {
    saved = {}
  }
  const colW = reactive(saved)
  function onHeaderDrag(newWidth, _oldWidth, column) {
    const key = column.property || column.label
    if (!key) return
    colW[key] = Math.round(newWidth)
    localStorage.setItem(storageKey, JSON.stringify(colW))
  }
  return { colW, onHeaderDrag }
}
