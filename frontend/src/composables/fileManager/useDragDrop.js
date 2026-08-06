// 文件管理器 · 拖拽（useFileManager 的内部实现，不单独对外用）。
// 两件事共用一个模块：把行拖到目标文件夹「拖动移动」，把外部文件拖进页面「拖拽上传」。
//
// 输入：
//   fm            fmApi 模块（move）
//   cwd           ref<string>  移动接口要的当前目录
//   selPaths / selRows / selectedPath   归 useSelection 所有：拖已选中的行 = 拖整个选中集；
//                                       移动掉的行若正被预览则收起面板
//   showInfo      ref<boolean> 同上
//   clearSel      useSelection 的清空
//   loadCwd / reloadTree   移动完刷新
//   uploadMany    useTransfers 的批量上传（拖拽上传的落点）
//
// 输出：move（reactive：dragging / rows / overPath，模板据此高亮落点）、
//   uploadHover（computed：是否正悬停着外部文件，显示拖入蒙层）、
//   onDragStartRow / onDragEndRow / onDestDragOver / onDestDragLeave / onDestDrop /
//   onRowDragOver / onRowDragLeave / onRowDrop / onDragEnter / onDragLeave / onDrop。
import { ref, reactive, computed } from 'vue'
import { ElMessage } from 'element-plus'

export function useDragDrop({
  fm,
  cwd,
  selPaths,
  selRows,
  selectedPath,
  showInfo,
  clearSel,
  loadCwd,
  reloadTree,
  uploadMany,
}) {
  // ---------- 拖动移动（把文件/文件夹拖到目标文件夹或左侧目录树）----------
  // move.rows 为正在拖的行集，move.overPath 为当前悬停的目标文件夹路径
  const move = reactive({ dragging: false, rows: [], overPath: null })

  function parentOf(p) {
    return p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : ''
  }
  // 拖的是已选中集合里的项 → 拖整个选中集；否则只拖这一行
  function dragSetFor(row) {
    return selPaths.value.length > 1 && selPaths.value.includes(row.path) ? selRows.value : [row]
  }
  // dest 是否是合法落点：非自身/子孙、且不是原地（源就在该目录下）
  function canDropDest(dest) {
    if (!move.dragging || dest === null) return false
    return !move.rows.some(
      (r) =>
        r.path === dest || parentOf(r.path) === dest || (r.is_dir && dest.startsWith(r.path + '/')),
    )
  }
  function onDragStartRow(ev, row) {
    move.rows = dragSetFor(row)
    move.dragging = true
    ev.dataTransfer.effectAllowed = 'move'
    ev.dataTransfer.setData('application/x-fm-move', '1') // 标记为内部拖拽，区别于外部拖文件上传
    const n = move.rows.length
    if (n > 1 && ev.dataTransfer.setDragImage) {
      const chip = document.createElement('div')
      chip.textContent = `移动 ${n} 项`
      chip.style.cssText =
        'position:fixed;top:-1000px;left:-1000px;padding:4px 10px;border-radius:6px;' +
        'background:var(--el-color-primary,#1677ff);color:#fff;font-size:12px;font-weight:600;'
      document.body.appendChild(chip)
      ev.dataTransfer.setDragImage(chip, -8, -8)
      setTimeout(() => chip.remove(), 0)
    }
  }
  function onDragEndRow() {
    move.dragging = false
    move.rows = []
    move.overPath = null
  }
  function onDestDragOver(ev, dest) {
    if (!canDropDest(dest)) return
    ev.preventDefault()
    ev.stopPropagation() // 别冒泡到 fm-main 的上传拖放
    ev.dataTransfer.dropEffect = 'move'
    move.overPath = dest
  }
  function onDestDragLeave(dest) {
    if (move.overPath === dest) move.overPath = null
  }
  async function onDestDrop(ev, dest) {
    if (!canDropDest(dest)) return
    ev.preventDefault()
    ev.stopPropagation()
    const rows = move.rows.slice()
    onDragEndRow()
    await moveInto(dest, rows)
  }
  // 行/卡片作落点时，仅文件夹可接收
  function onRowDragOver(ev, row) {
    if (row.is_dir) onDestDragOver(ev, row.path)
  }
  function onRowDragLeave(row) {
    if (row.is_dir) onDestDragLeave(row.path)
  }
  function onRowDrop(ev, row) {
    if (row.is_dir) onDestDrop(ev, row.path)
  }
  async function moveInto(dest, rows) {
    const movable = rows.filter(
      (r) =>
        parentOf(r.path) !== dest &&
        r.path !== dest &&
        !(r.is_dir && dest.startsWith(r.path + '/')),
    )
    if (!movable.length) return
    try {
      await fm.move(
        cwd.value,
        dest,
        movable.map((r) => r.path),
      )
      ElMessage.success(`已移动 ${movable.length} 项`)
      if (movable.some((r) => r.path === selectedPath.value)) {
        showInfo.value = false
        selectedPath.value = ''
      }
      clearSel()
      await loadCwd()
      if (movable.some((r) => r.is_dir)) reloadTree()
    } catch {
      // 失败已由 axios 拦截器统一提示
    }
  }

  // ---------- 拖拽上传（保留文件夹结构） ----------
  const dragDepth = ref(0)
  const uploadHover = computed(() => dragDepth.value > 0)
  function hasFiles(ev) {
    return [...(ev.dataTransfer?.types || [])].includes('Files')
  }
  function onDragEnter(ev) {
    if (hasFiles(ev)) dragDepth.value++
  }
  function onDragLeave(ev) {
    if (hasFiles(ev) && dragDepth.value > 0) dragDepth.value--
  }
  async function onDrop(ev) {
    dragDepth.value = 0
    // DataTransferItemList 在首个 await 后就失效，必须先同步取完 entry
    const items = [...(ev.dataTransfer?.items || [])]
    const roots = items.map((it) => it.webkitGetAsEntry?.()).filter(Boolean)
    const flat = roots.length ? [] : [...(ev.dataTransfer?.files || [])]
    const out = []
    try {
      for (const e of roots) await walkEntry(e, '', out)
    } catch {
      return ElMessage.error('读取拖入内容失败')
    }
    for (const f of flat) out.push({ file: f, name: f.name })
    if (!out.length) return
    await uploadMany(out)
  }
  // FileSystemEntry 递归展开；readEntries 每批最多 100 条，需循环读空
  async function walkEntry(entry, prefix, out) {
    if (entry.isFile) {
      const f = await new Promise((res, rej) => entry.file(res, rej))
      out.push({ file: f, name: prefix ? `${prefix}/${f.name}` : f.name })
    } else if (entry.isDirectory) {
      const dirPath = prefix ? `${prefix}/${entry.name}` : entry.name
      const reader = entry.createReader()
      for (;;) {
        const batch = await new Promise((res, rej) => reader.readEntries(res, rej))
        if (!batch.length) break
        for (const e of batch) await walkEntry(e, dirPath, out)
      }
    }
  }

  return {
    move,
    uploadHover,
    onDragStartRow,
    onDragEndRow,
    onDestDragOver,
    onDestDragLeave,
    onDestDrop,
    onRowDragOver,
    onRowDragLeave,
    onRowDrop,
    onDragEnter,
    onDragLeave,
    onDrop,
  }
}
