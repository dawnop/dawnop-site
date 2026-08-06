// 文件管理器 · 选择与框选（useFileManager 的内部实现，不单独对外用）。
//
// 输入（全部由 useFileManager 传入的共享 ref / 值）：
//   entries    ref<Row[]>   当前目录全部行（selRows / selFiles 从中筛）
//   filtered   computed<Row[]>  过滤后的行，顺序与 DOM 一致，框选按下标映射
//   viewMode   ref<'list'|'grid'>  列表用 el-table 的勾选，网格自己维护 selPaths
//   isMobile   ref<boolean> 移动端不启动框选（改「选择」模式）
//   tableRef   ref<ElTable> 列表模式下同步勾选
//   contentEl  ref<HTMLElement> 框选求交的容器
//   showInfo   ref<boolean> 点空白处顺带收起预览面板
//
// 输出：selectedPath / selPaths / selectMode / marquee / marqueeStyle / selected /
//   selRows / selFiles / rowClass / onSelChange / clearSel / toggleCheck /
//   toggleSelectMode / onBlankClick / onContentMousedown。
// 本模块自己挂 onUnmounted，兜底摘掉框选拖拽期间挂在 document 上的监听。
import { ref, reactive, computed, watch, onUnmounted } from 'vue'

export function useSelection({
  entries,
  filtered,
  viewMode,
  isMobile,
  tableRef,
  contentEl,
  showInfo,
}) {
  const selectedPath = ref('') // 预览用的单选
  const selPaths = ref([]) // 多选（批量操作用）
  const selectMode = ref(false) // 移动端多选模式（替代框选）

  // 单选那一行的完整数据（右侧预览面板用）
  const selected = computed(() => entries.value.find((r) => r.path === selectedPath.value) || null)
  const selRows = computed(() => entries.value.filter((r) => selPaths.value.includes(r.path)))
  const selFiles = computed(() => selRows.value.filter((r) => !r.is_dir))
  const rowClass = ({ row }) => (row.path === selectedPath.value ? 'is-sel' : '')

  function onSelChange(rows) {
    selPaths.value = rows.map((r) => r.path)
  }
  function clearSel() {
    selPaths.value = []
    tableRef.value?.clearSelection?.()
  }
  watch(viewMode, clearSel) // 列表/网格的选中态不互通，切换时清空

  function toggleCheck(row) {
    const i = selPaths.value.indexOf(row.path)
    if (i >= 0) selPaths.value = selPaths.value.filter((p) => p !== row.path)
    else selPaths.value = [...selPaths.value, row.path]
  }
  function toggleSelectMode() {
    selectMode.value = !selectMode.value
    if (!selectMode.value) clearSel()
  }

  // 点内容区空白处取消多选和单选（行/卡片/操作控件上的点击不算）
  let marqueeSuppressBlank = false
  function onBlankClick(ev) {
    // 框选拖拽结束会补发一次 click，别把刚框中的选择清掉
    if (marqueeSuppressBlank) {
      marqueeSuppressBlank = false
      return
    }
    if (ev.target.closest('.el-table__row, .cell, .rowmore, .el-dropdown, .el-checkbox')) return
    clearSel()
    selectedPath.value = ''
    showInfo.value = false
  }

  // ---------- 框选多选（在空白处按下并拖动画选择框）----------
  // 框选：起止点（视口坐标），show 为是否正在画框
  const marquee = reactive({ show: false, x0: 0, y0: 0, x1: 0, y1: 0 })
  const marqueeStyle = computed(() => ({
    left: Math.min(marquee.x0, marquee.x1) + 'px',
    top: Math.min(marquee.y0, marquee.y1) + 'px',
    width: Math.abs(marquee.x1 - marquee.x0) + 'px',
    height: Math.abs(marquee.y1 - marquee.y0) + 'px',
  }))

  // 拖拽中的收尾函数。正常在 mouseup 时自己清空，中途卸载则由 onUnmounted 调用，
  // 避免 document 上留下监听。
  let dragCleanup = null

  function onContentMousedown(ev) {
    if (ev.button !== 0) return
    if (isMobile.value) return // 移动端不启动框选，改用「选择」模式
    // 落在行/卡片/表头/控件上时不启动框选，交给点击或拖拽
    if (
      ev.target.closest(
        '.el-table__row, .cell, .el-table__header, .rowmore, .el-checkbox, .el-dropdown, a, button, input',
      )
    )
      return
    // 上一轮拖拽若在内容区外松手，就不会补发 click，抑制标志会一直挂着，把下一次
    // 真正的空白点击吃掉（表现为「偶尔点空白清不掉选中」）。每次按下先归零。
    marqueeSuppressBlank = false
    const sx = ev.clientX
    const sy = ev.clientY
    let moved = false
    const onMove = (e) => {
      if (!moved) {
        if (Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy) < 5) return // 抖动阈值
        moved = true
        marquee.show = true
        document.body.style.userSelect = 'none'
      }
      marquee.x0 = sx
      marquee.y0 = sy
      marquee.x1 = e.clientX
      marquee.y1 = e.clientY
      applyMarquee()
    }
    const onUp = () => {
      dragCleanup = null
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.style.userSelect = ''
      if (moved) {
        marquee.show = false
        marqueeSuppressBlank = true
      }
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    // 这两个监听挂在 document 上，只有 onUp 会摘。拖拽途中离开本页（如按 Esc 走路由）
    // 就永远等不到 onUp，监听连同整个 composable 闭包留在 document 上继续改 marquee。
    // 交给 onUnmounted 兜底。
    dragCleanup = onUp
  }
  // 选择框与各行/卡片求交（DOM 顺序与 filtered 一致，按下标映射回数据行）
  function applyMarquee() {
    const rows = filtered.value
    const nodes = contentEl.value?.querySelectorAll(
      viewMode.value === 'list' ? '.el-table__body-wrapper .el-table__row' : '.cell',
    )
    if (!nodes) return
    const L = Math.min(marquee.x0, marquee.x1)
    const R = Math.max(marquee.x0, marquee.x1)
    const T = Math.min(marquee.y0, marquee.y1)
    const B = Math.max(marquee.y0, marquee.y1)
    const hit = new Set()
    nodes.forEach((el, i) => {
      const b = el.getBoundingClientRect()
      if (b.right >= L && b.left <= R && b.bottom >= T && b.top <= B && rows[i])
        hit.add(rows[i].path)
    })
    if (viewMode.value === 'list' && tableRef.value) {
      // 交给 el-table 勾选，selPaths 由 selection-change 同步
      rows.forEach((row) => tableRef.value.toggleRowSelection(row, hit.has(row.path)))
    } else {
      selPaths.value = [...hit]
    }
  }

  onUnmounted(() => dragCleanup?.())

  return {
    selectedPath,
    selPaths,
    selectMode,
    marquee,
    marqueeStyle,
    selected,
    selRows,
    selFiles,
    rowClass,
    onSelChange,
    clearSel,
    toggleCheck,
    toggleSelectMode,
    onBlankClick,
    onContentMousedown,
  }
}
