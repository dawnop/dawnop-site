// 文件管理器领域逻辑：状态 + 加载 / 选择 / 增删改 / 上传下载 / 移动复制 / 拖拽 / 框选 / 右键菜单。
// 从 FilesLabView.vue 抽出，与视图（模板 / 样式）解耦，便于单测与复用。
// 视图侧只做「解构本 composable 的返回值 + 渲染」。
import { ref, reactive, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import {
  Folder,
  FolderOpened,
  FolderAdd,
  Document,
  Picture,
  Files,
  Grid,
  List,
  Download,
  EditPen,
  Delete,
  View,
  RefreshRight,
  Right,
  CopyDocument,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { fmApi as fm, settingsApi } from '../api'
import { useIsMobile } from './useIsMobile'
import { fmtBytes, fmtMonthDay } from '../utils/format'

export function useFileManager() {
  // ---------- 状态 ----------
  const cwd = ref('') // 当前目录相对路径，''=根
  const entries = ref([])
  const loading = ref(false)
  const q = ref('')
  const viewMode = ref('list') // list | grid
  const showInfo = ref(false) // 预览面板默认收起，点文件才弹出
  const selectedPath = ref('') // 预览用的单选
  const selPaths = ref([]) // 多选（批量操作用）
  const previewText = ref('')
  const previewErr = ref('')

  // ---------- 移动端 ----------
  const isMobile = useIsMobile()
  const selectMode = ref(false) // 移动端多选模式（替代框选）
  const searchOpen = ref(false) // 移动端搜索栏展开（默认收起，点右下角搜索按钮展开）
  const searchInputRef = ref(null)
  const sheet = reactive({ show: false, row: null }) // 底部操作弹层（替代右键菜单 + 右侧预览面板）

  function openSearch() {
    searchOpen.value = true
    nextTick(() => searchInputRef.value?.focus())
  }
  function closeSearch() {
    searchOpen.value = false
    q.value = ''
  }
  // 移动端强制网格视图（表格在窄屏太挤）
  const effectiveView = computed(() => (isMobile.value ? 'grid' : viewMode.value))

  // 存储用量（侧栏用量条）与全局配置（并发数、文本预览上限）
  const drive = ref(null)
  const conf = reactive({
    upload_concurrency: 3,
    download_concurrency: 3,
    text_preview_max_kb: 512,
  })
  const drivePct = computed(() =>
    drive.value?.quota
      ? Math.min(100, Math.round((drive.value.used / drive.value.quota) * 100))
      : 0,
  )
  async function loadStats() {
    try {
      drive.value = await fm.stats()
    } catch {
      /* 用量条展示失败不打扰 */
    }
  }
  async function loadConf() {
    try {
      Object.assign(conf, (await settingsApi.get()).data)
    } catch {
      /* 用默认值 */
    }
  }

  // 传输列表：上传/下载每个文件一条任务，带各自进度
  const tasks = ref([])
  const tasksCollapsed = ref(false)
  let taskSeq = 0
  function addTask(kind, name) {
    const t = reactive({ id: ++taskSeq, kind, name, pct: 0, status: 'active', err: '' })
    tasks.value.push(t)
    return t
  }
  const activeCount = computed(() => tasks.value.filter((t) => t.status === 'active').length)

  const treeRef = ref(null)
  const treeKey = ref(0) // 目录变动时 bump 强制重挂树
  const tableRef = ref(null)
  const fileInput = ref(null)
  const dirInput = ref(null)

  // 面板宽度（可拖拽，持久化）
  function clampW(v, min, max, dft) {
    return Number.isFinite(v) && v > 0 ? Math.min(max, Math.max(min, v)) : dft
  }
  const sideW = ref(clampW(+localStorage.getItem('fm_side_w'), 160, 380, 220))
  const infoW = ref(clampW(+localStorage.getItem('fm_info_w'), 220, 520, 288))

  const viewOptions = [
    { value: 'list', icon: List },
    { value: 'grid', icon: Grid },
  ]
  const treeProps = { label: 'name', isLeaf: 'leaf' }

  // ---------- 派生 ----------
  const filtered = computed(() => {
    const kw = q.value.trim().toLowerCase()
    return kw ? entries.value.filter((r) => r.name.toLowerCase().includes(kw)) : entries.value
  })
  const selected = computed(() => entries.value.find((r) => r.path === selectedPath.value) || null)
  const selRows = computed(() => entries.value.filter((r) => selPaths.value.includes(r.path)))
  const selFiles = computed(() => selRows.value.filter((r) => !r.is_dir))

  // 拖动移动：dnd.rows 为正在拖的行集，overPath 为当前悬停的目标文件夹路径
  const contentEl = ref(null)
  const dnd = reactive({ dragging: false, rows: [], overPath: null })
  // 框选：起止点（视口坐标），show 为是否正在画框
  const marquee = reactive({ show: false, x0: 0, y0: 0, x1: 0, y1: 0 })
  const marqueeStyle = computed(() => ({
    left: Math.min(marquee.x0, marquee.x1) + 'px',
    top: Math.min(marquee.y0, marquee.y1) + 'px',
    width: Math.abs(marquee.x1 - marquee.x0) + 'px',
    height: Math.abs(marquee.y1 - marquee.y0) + 'px',
  }))
  const crumbs = computed(() => {
    const segs = cwd.value.split('/').filter(Boolean)
    const acc = [{ name: '我的文件', path: '' }]
    let p = ''
    for (const s of segs) {
      p = p ? `${p}/${s}` : s
      acc.push({ name: s, path: p })
    }
    return acc
  })
  const rowClass = ({ row }) => (row.path === selectedPath.value ? 'is-sel' : '')

  // ---------- 加载 ----------
  // 请求序号：快速连点两个目录时，先发的可能后到，把后发的结果盖掉——列表显示 A 的行，
  // 面包屑/cwd 却是 B，随后的删除/改名都会拿 B 当基准去操作 A 的行。丢弃过期响应即可。
  let cwdReq = 0
  async function loadCwd() {
    const my = ++cwdReq
    loading.value = true
    try {
      const rows = await fm.listDir(cwd.value)
      if (my !== cwdReq) return // 已有更新的请求在飞，这份结果作废
      entries.value = rows
    } catch {
      // 失败已由 axios 拦截器统一提示，这里只需结束 loading
    } finally {
      if (my === cwdReq) loading.value = false
    }
    if (my !== cwdReq) return
    loadStats() // 每次目录变动/增删改后顺带刷新用量条（不阻塞列表）
  }
  function goto(path) {
    cwd.value = path
    selectedPath.value = ''
    q.value = ''
    clearSel()
    loadCwd()
  }

  // 目录树懒加载
  async function loadTreeNode(node, resolve) {
    if (node.level === 0) return resolve([{ name: '我的文件', path: '' }])
    try {
      const rows = await fm.listDir(node.data.path)
      resolve(rows.filter((r) => r.is_dir).map((r) => ({ name: r.name, path: r.path })))
    } catch {
      resolve([])
    }
  }
  function onTreeClick(data) {
    goto(data.path)
  }
  function reloadTree() {
    treeKey.value++
  } // 文件夹增删改后重建树

  // ---------- 选择 ----------
  function onSelChange(rows) {
    selPaths.value = rows.map((r) => r.path)
  }
  function clearSel() {
    selPaths.value = []
    tableRef.value?.clearSelection?.()
  }
  watch(viewMode, clearSel) // 列表/网格的选中态不互通，切换时清空

  // ---------- 行为 ----------
  function textTooLarge(row) {
    return row.size > conf.text_preview_max_kb * 1024
  }
  async function selectFile(row) {
    selectedPath.value = row.path
    if (row.is_dir) return
    showInfo.value = true
    previewText.value = ''
    previewErr.value = ''
    if (isText(row)) {
      if (textTooLarge(row)) {
        previewErr.value = `文件超过 ${conf.text_preview_max_kb} KB，请下载查看`
        return
      }
      try {
        const text = await fm.textContent(row.path)
        if (selectedPath.value !== row.path) return // 期间已选中别的文件，这份作废
        previewText.value = text
      } catch (e) {
        if (selectedPath.value !== row.path) return
        previewErr.value = e.message || '预览失败'
      }
    }
  }
  // 单击延迟执行、双击时取消：否则双击的第一下就弹出右侧预览面板，
  // 表格随之变窄、行发生位移，第二下会点到别处，双击时灵时不灵。
  let clickTimer = null
  function onRowClick(row) {
    clearTimeout(clickTimer)
    clickTimer = setTimeout(() => selectFile(row), 250)
  }
  function onRowDblclick(row) {
    clearTimeout(clickTimer)
    row.is_dir ? goto(row.path) : openModal(row)
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

  // 双击预览：图片走 el-image-viewer（滚轮/按钮缩放），文本走弹窗（可编辑），其余给下载入口
  const imgViewer = reactive({ show: false, urls: [] })
  function openImgViewer(row) {
    imgViewer.urls = [fm.previewUrl(row.path)]
    imgViewer.show = true
  }
  const modal = reactive({
    show: false,
    row: null,
    text: '',
    err: '',
    loaded: false,
    editing: false,
    draft: '',
    saving: false,
  })
  async function openModal(row) {
    selectedPath.value = row.path
    if (isImage(row)) return openImgViewer(row)
    modal.row = row
    modal.text = ''
    modal.err = ''
    modal.loaded = false
    modal.editing = false
    modal.show = true
    if (isText(row)) {
      if (textTooLarge(row)) {
        modal.err = `文件超过 ${conf.text_preview_max_kb} KB，请下载查看`
        return
      }
      try {
        const text = await fm.textContent(row.path)
        // 取字节是「签名 + 直连七牛」两跳（上限 8s），慢到足以被下面这串操作插队：
        // 开 A（大文件，还在飞）→ Esc/点遮罩关掉 → 开 B（小文件，秒回）→ A 才回来。
        // 若不认领，A 的正文就落进标题是 B 的弹窗里；再「编辑 → 保存」写的是 modal.row.path，
        // 即把 A 的内容存进 B——静默覆盖。认领一下，过期响应直接丢。
        if (modal.row !== row) return
        modal.text = text
        modal.loaded = true
      } catch (e) {
        if (modal.row !== row) return
        modal.err = e.message || '预览失败'
      }
    }
  }
  function startEdit() {
    modal.draft = modal.text
    modal.editing = true
  }
  async function saveEdit() {
    modal.saving = true
    try {
      await fm.saveText(modal.row.path, modal.draft)
      modal.text = modal.draft
      modal.editing = false
      ElMessage.success('已保存')
      if (selectedPath.value === modal.row.path) previewText.value = modal.text
      loadCwd() // 大小变了，刷新列表
    } catch {
      // 失败已由 axios 拦截器统一提示
    } finally {
      modal.saving = false
    }
  }
  // 编辑中有未保存改动时，关弹窗先确认
  function beforeCloseModal(done) {
    if (modal.editing && modal.draft !== modal.text) {
      ElMessageBox.confirm('有未保存的修改，确定关闭？', '关闭预览', {
        type: 'warning',
        confirmButtonText: '关闭',
        cancelButtonText: '继续编辑',
      })
        .then(done)
        .catch(() => {})
    } else {
      done()
    }
  }
  // 网格：Ctrl/⌘ 点击加入多选，普通点击走预览
  function onCellClick(row, ev) {
    if (ev && (ev.ctrlKey || ev.metaKey)) {
      const i = selPaths.value.indexOf(row.path)
      if (i >= 0) selPaths.value.splice(i, 1)
      else selPaths.value.push(row.path)
      return
    }
    onRowClick(row)
  }

  // 移动端：点卡片 = 打开（文件夹进入 / 文件预览）；选择模式下 = 勾选
  function onCellTap(row, ev) {
    if (!isMobile.value) return onCellClick(row, ev)
    if (selectMode.value) return toggleCheck(row)
    if (row.is_dir) goto(row.path)
    else if (isImage(row)) openImgViewer(row)
    else openModal(row)
  }
  function toggleCheck(row) {
    const i = selPaths.value.indexOf(row.path)
    if (i >= 0) selPaths.value = selPaths.value.filter((p) => p !== row.path)
    else selPaths.value = [...selPaths.value, row.path]
  }
  function toggleSelectMode() {
    selectMode.value = !selectMode.value
    if (!selectMode.value) clearSel()
  }
  // 底部操作弹层
  function openSheet(row) {
    sheet.row = row
    sheet.show = true
  }
  function sheetDo(action) {
    const row = sheet.row
    sheet.show = false
    if (!row) return
    if (action === 'preview') {
      if (isImage(row)) openImgViewer(row)
      else openModal(row)
    } else if (action === 'download') doDownload(row)
    else if (action === 'rename') doRename(row)
    else if (action === 'move') startMoveCopy('move', [row])
    else if (action === 'copy') startMoveCopy('copy', [row])
    else if (action === 'delete') doDelete(row)
  }

  // 下载：直连七牛流式取字节（传输列表里显示进度），失败回退 302 浏览器直接下载
  async function doDownload(row) {
    const t = addTask('down', row.name)
    try {
      const blob = await fm.downloadBlob(row.path, (p) => (t.pct = Math.round(p * 100)), row.size)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = row.name
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 60000)
      t.pct = 100
      t.status = 'done'
    } catch {
      t.status = 'error'
      t.err = '进度不可用，已转浏览器直接下载'
      const a = document.createElement('a')
      a.href = fm.downloadUrl(row.path)
      a.rel = 'noopener'
      document.body.appendChild(a)
      a.click()
      a.remove()
    }
  }
  // 批量下载：并发数走全局配置
  async function doDownloadMany(rows) {
    const files = rows.filter((r) => !r.is_dir)
    if (!files.length) return ElMessage.warning('选中项里没有可下载的文件')
    let next = 0
    async function worker() {
      for (;;) {
        const i = next++
        if (i >= files.length) return
        await doDownload(files[i])
      }
    }
    await Promise.all(
      Array.from({ length: Math.min(conf.download_concurrency, files.length) }, worker),
    )
  }

  async function newFolder() {
    try {
      const { value } = await ElMessageBox.prompt('文件夹名称', '新建文件夹', {
        confirmButtonText: '创建',
        cancelButtonText: '取消',
        inputPattern: /.+/,
        inputErrorMessage: '名称不能为空',
      })
      await fm.createFolder(cwd.value, value.trim())
      ElMessage.success('已创建')
      await loadCwd()
      reloadTree()
    } catch {
      /* 取消或失败：失败已由 axios 拦截器统一提示 */
    }
  }

  async function doRename(row) {
    try {
      const { value } = await ElMessageBox.prompt('新名称', '重命名', {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        inputValue: row.name,
        inputPattern: /.+/,
        inputErrorMessage: '名称不能为空',
      })
      const name = value.trim()
      if (name === row.name) return
      await fm.rename(cwd.value, row.path, name)
      ElMessage.success('已重命名')
      await loadCwd()
      if (row.is_dir) reloadTree()
    } catch {
      /* 取消或失败：失败已由 axios 拦截器统一提示 */
    }
  }

  async function doDelete(row) {
    try {
      await ElMessageBox.confirm(
        `确定删除「${row.name}」？${row.is_dir ? '（含其中全部内容）' : ''}`,
        '删除',
        {
          type: 'warning',
          confirmButtonText: '删除',
          cancelButtonText: '取消',
          confirmButtonClass: 'el-button--danger',
        },
      )
      await fm.remove(cwd.value, [row.path])
      ElMessage.success('已删除')
      if (selectedPath.value === row.path) {
        showInfo.value = false
        selectedPath.value = ''
      }
      clearSel()
      await loadCwd()
      if (row.is_dir) reloadTree()
    } catch {
      /* 取消或失败：失败已由 axios 拦截器统一提示 */
    }
  }

  async function doDeleteMany(rows) {
    try {
      await ElMessageBox.confirm(
        `确定删除选中的 ${rows.length} 项？（文件夹含其中全部内容）`,
        '批量删除',
        {
          type: 'warning',
          confirmButtonText: '删除',
          cancelButtonText: '取消',
          confirmButtonClass: 'el-button--danger',
        },
      )
      await fm.remove(
        cwd.value,
        rows.map((r) => r.path),
      )
      ElMessage.success(`已删除 ${rows.length} 项`)
      if (rows.some((r) => r.path === selectedPath.value)) {
        showInfo.value = false
        selectedPath.value = ''
      }
      const hadDir = rows.some((r) => r.is_dir)
      clearSel()
      await loadCwd()
      if (hadDir) reloadTree()
    } catch {
      /* 取消或失败：失败已由 axios 拦截器统一提示 */
    }
  }

  function onRowCommand(cmd, row) {
    if (cmd === '下载') doDownload(row)
    else if (cmd === '重命名') doRename(row)
    else if (cmd === '删除') doDelete(row)
  }

  // ---------- 移动 / 复制（目标目录选择弹窗） ----------
  const destDlg = reactive({ show: false, mode: 'move', rows: [], path: null, busy: false })
  function startMoveCopy(mode, rows) {
    destDlg.mode = mode
    destDlg.rows = rows
    destDlg.path = null
    destDlg.show = true
  }
  async function confirmDest() {
    const dest = destDlg.path
    const rows = destDlg.rows
    const verb = destDlg.mode === 'move' ? '移动' : '复制'
    for (const r of rows) {
      if (r.is_dir && (dest === r.path || dest.startsWith(r.path + '/'))) {
        return ElMessage.warning(`不能${verb}到「${r.name}」自身内部`)
      }
    }
    if (destDlg.mode === 'move' && dest === cwd.value) return ElMessage.warning('已在当前目录')
    destDlg.busy = true
    try {
      await fm[destDlg.mode](
        cwd.value,
        dest,
        rows.map((r) => r.path),
      )
      ElMessage.success(`已${verb} ${rows.length} 项`)
      destDlg.show = false
      if (rows.some((r) => r.path === selectedPath.value)) {
        showInfo.value = false
        selectedPath.value = ''
      }
      clearSel()
      await loadCwd()
      if (rows.some((r) => r.is_dir)) reloadTree()
    } catch {
      // 失败已由 axios 拦截器统一提示
    } finally {
      destDlg.busy = false
    }
  }

  // ---------- 拖动移动（把文件/文件夹拖到目标文件夹或左侧目录树）----------
  function parentOf(p) {
    return p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : ''
  }
  // 拖的是已选中集合里的项 → 拖整个选中集；否则只拖这一行
  function dragSetFor(row) {
    return selPaths.value.length > 1 && selPaths.value.includes(row.path) ? selRows.value : [row]
  }
  // dest 是否是合法落点：非自身/子孙、且不是原地（源就在该目录下）
  function canDropDest(dest) {
    if (!dnd.dragging || dest === null) return false
    return !dnd.rows.some(
      (r) =>
        r.path === dest || parentOf(r.path) === dest || (r.is_dir && dest.startsWith(r.path + '/')),
    )
  }
  function onDragStartRow(ev, row) {
    dnd.rows = dragSetFor(row)
    dnd.dragging = true
    ev.dataTransfer.effectAllowed = 'move'
    ev.dataTransfer.setData('application/x-fm-move', '1') // 标记为内部拖拽，区别于外部拖文件上传
    const n = dnd.rows.length
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
    dnd.dragging = false
    dnd.rows = []
    dnd.overPath = null
  }
  function onDestDragOver(ev, dest) {
    if (!canDropDest(dest)) return
    ev.preventDefault()
    ev.stopPropagation() // 别冒泡到 fm-main 的上传拖放
    ev.dataTransfer.dropEffect = 'move'
    dnd.overPath = dest
  }
  function onDestDragLeave(dest) {
    if (dnd.overPath === dest) dnd.overPath = null
  }
  async function onDestDrop(ev, dest) {
    if (!canDropDest(dest)) return
    ev.preventDefault()
    ev.stopPropagation()
    const rows = dnd.rows.slice()
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

  // ---------- 框选多选（在空白处按下并拖动画选择框）----------
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

  // ---------- 右键菜单 ----------
  const ctx = reactive({ show: false, x: 0, y: 0, rows: [] })
  function openCtx(ev, rows) {
    ev.preventDefault()
    ev.stopPropagation()
    ctx.rows = rows
    // 贴边时向内收，避免菜单出屏
    ctx.x = Math.min(ev.clientX, window.innerWidth - 200)
    ctx.y = Math.min(ev.clientY, window.innerHeight - (ctxItems.value.length * 34 + 24))
    ctx.show = true
  }
  function openCtxRow(ev, row) {
    // 右键落在已多选的行上 → 对整个选中集操作；否则单行
    if (selPaths.value.length > 1 && selPaths.value.includes(row.path)) {
      openCtx(ev, selRows.value)
    } else {
      selectedPath.value = row.path
      openCtx(ev, [row])
    }
  }
  function openCtxBlank(ev) {
    openCtx(ev, [])
  }
  function onTableRowCtx(row, _col, ev) {
    openCtxRow(ev, row)
  }
  function closeCtx() {
    ctx.show = false
  }
  function runCtx(item) {
    closeCtx()
    item.run()
  }
  function onGlobalKey(e) {
    if (e.key === 'Escape') closeCtx()
  }

  const ctxItems = computed(() => {
    const rows = ctx.rows
    if (!rows.length) {
      return [
        { label: '新建文件夹', icon: FolderAdd, run: newFolder },
        { label: '上传文件', icon: Files, run: pickFiles },
        { label: '上传文件夹', icon: FolderOpened, run: pickFolder },
        { label: '刷新', icon: RefreshRight, divided: true, run: loadCwd },
      ]
    }
    if (rows.length === 1) {
      const r = rows[0]
      const items = []
      if (r.is_dir) items.push({ label: '打开', icon: FolderOpened, run: () => goto(r.path) })
      else {
        items.push({ label: '预览', icon: View, run: () => selectFile(r) })
        items.push({ label: '下载', icon: Download, run: () => doDownload(r) })
      }
      items.push({ label: '重命名', icon: EditPen, run: () => doRename(r) })
      items.push({ label: '移动到…', icon: Right, run: () => startMoveCopy('move', [r]) })
      items.push({ label: '复制到…', icon: CopyDocument, run: () => startMoveCopy('copy', [r]) })
      items.push({
        label: '删除',
        icon: Delete,
        danger: true,
        divided: true,
        run: () => doDelete(r),
      })
      return items
    }
    const files = rows.filter((r) => !r.is_dir)
    return [
      ...(files.length
        ? [
            {
              label: `下载 ${files.length} 个文件`,
              icon: Download,
              run: () => doDownloadMany(rows),
            },
          ]
        : []),
      { label: `移动 ${rows.length} 项到…`, icon: Right, run: () => startMoveCopy('move', rows) },
      {
        label: `复制 ${rows.length} 项到…`,
        icon: CopyDocument,
        run: () => startMoveCopy('copy', rows),
      },
      {
        label: `删除 ${rows.length} 项`,
        icon: Delete,
        danger: true,
        divided: true,
        run: () => doDeleteMany(rows),
      },
    ]
  })

  // ---------- 上传（直传七牛，支持文件夹；并行 3 路） ----------
  function pickFiles() {
    fileInput.value?.click()
  }
  function pickFolder() {
    dirInput.value?.click()
  }
  async function onFilesPicked(ev) {
    const files = [...(ev.target.files || [])]
    ev.target.value = ''
    if (!files.length) return
    await uploadMany(files.map((f) => ({ file: f, name: f.webkitRelativePath || f.name })))
  }

  // list: [{file, name}]，name 为相对 cwd 的路径（含子目录时后端自动建目录）。
  // 并发数走全局配置，每个文件在传输列表里一条任务、独立进度，失败不中断其余。
  async function uploadMany(list) {
    const dir = cwd.value
    const items = list.map((x) => ({ ...x, task: addTask('up', x.name || x.file.name) }))
    let next = 0
    let failed = 0
    async function worker() {
      for (;;) {
        const i = next++
        if (i >= items.length) return
        const { file, name, task } = items[i]
        try {
          await fm.uploadFile(dir, file, (p) => (task.pct = Math.round(p * 100)), name)
          task.pct = 100
          task.status = 'done'
        } catch (e) {
          failed++
          task.status = 'error'
          task.err = e?.response?.data?.detail || e.message || '上传失败'
        }
      }
    }
    await Promise.all(
      Array.from({ length: Math.min(conf.upload_concurrency, items.length) }, worker),
    )
    if (failed) ElMessage.warning(`上传完成，${failed} 个失败（详见传输列表）`)
    else ElMessage.success(`上传完成（${items.length} 个）`)
    const hadFolder = list.some((x) => (x.name || '').includes('/'))
    if (cwd.value === dir) {
      await loadCwd()
      if (hadFolder) reloadTree()
    } else if (hadFolder) reloadTree()
  }

  // ---------- 拖拽上传（保留文件夹结构） ----------
  const dragDepth = ref(0)
  const dragging = computed(() => dragDepth.value > 0)
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

  // ---------- 面板宽度拖拽 ----------
  function startResize(which, ev) {
    const startX = ev.clientX
    const startW = which === 'side' ? sideW.value : infoW.value
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const move = (e) => {
      const d = e.clientX - startX
      if (which === 'side') sideW.value = Math.min(380, Math.max(160, startW + d))
      else infoW.value = Math.min(520, Math.max(220, startW - d)) // 右面板往左拖变宽
    }
    const up = () => {
      document.removeEventListener('mousemove', move)
      document.removeEventListener('mouseup', up)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      localStorage.setItem('fm_side_w', String(sideW.value))
      localStorage.setItem('fm_info_w', String(infoW.value))
    }
    document.addEventListener('mousemove', move)
    document.addEventListener('mouseup', up)
  }

  // ---------- 展示辅助 ----------
  function isImage(r) {
    return r.content_type?.startsWith('image/')
  }
  function isText(r) {
    return (
      /^text\//.test(r.content_type || '') ||
      /\.(md|txt|yml|yaml|json|log|csv|xml|ini|conf)$/i.test(r.name)
    )
  }
  function iconOf(r) {
    if (r.is_dir) return Folder
    if (isImage(r)) return Picture
    if (isText(r)) return Document
    return Files
  }
  function tintOf(r) {
    if (r.is_dir) return '#e8a13a'
    if (isImage(r)) return '#1677ff'
    if (isText(r)) return '#52a852'
    return '#9fa1ae'
  }

  onMounted(() => {
    loadCwd()
    loadConf()
    document.addEventListener('click', closeCtx)
    document.addEventListener('keydown', onGlobalKey)
  })
  onUnmounted(() => {
    document.removeEventListener('click', closeCtx)
    document.removeEventListener('keydown', onGlobalKey)
  })

  return {
    // 直传模块（模板里 fm.previewUrl 等）
    fm,
    // 状态
    cwd,
    entries,
    loading,
    q,
    viewMode,
    showInfo,
    selectedPath,
    selPaths,
    previewText,
    previewErr,
    isMobile,
    selectMode,
    searchOpen,
    searchInputRef,
    sheet,
    effectiveView,
    drive,
    conf,
    drivePct,
    tasks,
    tasksCollapsed,
    activeCount,
    treeRef,
    treeKey,
    tableRef,
    fileInput,
    dirInput,
    sideW,
    infoW,
    viewOptions,
    treeProps,
    filtered,
    selected,
    selRows,
    selFiles,
    contentEl,
    dnd,
    marquee,
    marqueeStyle,
    crumbs,
    rowClass,
    imgViewer,
    modal,
    destDlg,
    dragDepth,
    dragging,
    ctx,
    ctxItems,
    // 方法
    openSearch,
    closeSearch,
    loadStats,
    loadConf,
    addTask,
    loadCwd,
    goto,
    loadTreeNode,
    onTreeClick,
    reloadTree,
    onSelChange,
    clearSel,
    textTooLarge,
    selectFile,
    onRowClick,
    onRowDblclick,
    onBlankClick,
    openImgViewer,
    openModal,
    startEdit,
    saveEdit,
    beforeCloseModal,
    onCellClick,
    onCellTap,
    toggleCheck,
    toggleSelectMode,
    openSheet,
    sheetDo,
    doDownload,
    doDownloadMany,
    newFolder,
    doRename,
    doDelete,
    doDeleteMany,
    onRowCommand,
    startMoveCopy,
    confirmDest,
    parentOf,
    dragSetFor,
    canDropDest,
    onDragStartRow,
    onDragEndRow,
    onDestDragOver,
    onDestDragLeave,
    onDestDrop,
    onRowDragOver,
    onRowDragLeave,
    onRowDrop,
    moveInto,
    onContentMousedown,
    applyMarquee,
    openCtx,
    openCtxRow,
    openCtxBlank,
    onTableRowCtx,
    closeCtx,
    runCtx,
    onGlobalKey,
    pickFiles,
    pickFolder,
    onFilesPicked,
    uploadMany,
    hasFiles,
    onDragEnter,
    onDragLeave,
    onDrop,
    walkEntry,
    startResize,
    isImage,
    isText,
    iconOf,
    tintOf,
    // 格式化（模板沿用旧名 fmtSize / fmtDate）
    fmtSize: fmtBytes,
    fmtDate: fmtMonthDay,
  }
}
