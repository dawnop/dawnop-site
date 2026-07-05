<script setup>
// 自建文件管理器（SVAR 观感 / Element Plus）。第三期：右键菜单、拖拽上传、
// 多选批量操作、面板宽度可拖拽、并行上传/批量下载。
// 布局：左「目录树 + 存储条」/ 顶「工具栏」/ 中「列表·网格」/ 右「点文件弹出的预览面板」。
import { ref, reactive, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import {
  Folder, FolderOpened, FolderAdd, Document, Picture, Files, Upload,
  Search, Grid, List, MoreFilled, Download, EditPen, Delete,
  View, Close, Loading, RefreshRight, Right, CopyDocument,
  ArrowDown, ArrowUp, Plus, Select,
} from '@element-plus/icons-vue'
import * as fm from '../../api/fmApi'
import { settingsApi } from '../../api'
import { useIsMobile } from '../../composables/useIsMobile'

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
const conf = reactive({ upload_concurrency: 3, download_concurrency: 3, text_preview_max_kb: 512 })
const drivePct = computed(() =>
  drive.value?.quota ? Math.min(100, Math.round((drive.value.used / drive.value.quota) * 100)) : 0,
)
async function loadStats() {
  try { drive.value = await fm.stats() } catch { /* 用量条展示失败不打扰 */ }
}
async function loadConf() {
  try { Object.assign(conf, (await settingsApi.get()).data) } catch { /* 用默认值 */ }
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
const sideW = ref(clampW(+localStorage.getItem('fm_side_w'), 160, 380, 220))
const infoW = ref(clampW(+localStorage.getItem('fm_info_w'), 220, 520, 288))
function clampW(v, min, max, dft) {
  return Number.isFinite(v) && v > 0 ? Math.min(max, Math.max(min, v)) : dft
}

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
  for (const s of segs) { p = p ? `${p}/${s}` : s; acc.push({ name: s, path: p }) }
  return acc
})
const rowClass = ({ row }) => (row.path === selectedPath.value ? 'is-sel' : '')

// ---------- 加载 ----------
async function loadCwd() {
  loading.value = true
  try {
    entries.value = await fm.listDir(cwd.value)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
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
function onTreeClick(data) { goto(data.path) }
function reloadTree() { treeKey.value++ } // 文件夹增删改后重建树

// ---------- 选择 ----------
function onSelChange(rows) { selPaths.value = rows.map((r) => r.path) }
function clearSel() {
  selPaths.value = []
  tableRef.value?.clearSelection?.()
}
watch(viewMode, clearSel) // 列表/网格的选中态不互通，切换时清空

// ---------- 行为 ----------
function textTooLarge(row) { return row.size > conf.text_preview_max_kb * 1024 }
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
    try { previewText.value = await fm.textContent(row.path) }
    catch (e) { previewErr.value = e.message || '预览失败' }
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
  if (marqueeSuppressBlank) { marqueeSuppressBlank = false; return }
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
  show: false, row: null, text: '', err: '', loaded: false,
  editing: false, draft: '', saving: false,
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
      modal.text = await fm.textContent(row.path)
      modal.loaded = true
    } catch (e) { modal.err = e.message || '预览失败' }
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
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    modal.saving = false
  }
}
// 编辑中有未保存改动时，关弹窗先确认
function beforeCloseModal(done) {
  if (modal.editing && modal.draft !== modal.text) {
    ElMessageBox.confirm('有未保存的修改，确定关闭？', '关闭预览', {
      type: 'warning', confirmButtonText: '关闭', cancelButtonText: '继续编辑',
    }).then(done).catch(() => {})
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
function openSheet(row) { sheet.row = row; sheet.show = true }
function sheetDo(action) {
  const row = sheet.row
  sheet.show = false
  if (!row) return
  if (action === 'preview') { if (isImage(row)) openImgViewer(row); else openModal(row) }
  else if (action === 'download') doDownload(row)
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
  await Promise.all(Array.from({ length: Math.min(conf.download_concurrency, files.length) }, worker))
}

async function newFolder() {
  try {
    const { value } = await ElMessageBox.prompt('文件夹名称', '新建文件夹', {
      confirmButtonText: '创建', cancelButtonText: '取消',
      inputPattern: /.+/, inputErrorMessage: '名称不能为空',
    })
    await fm.createFolder(cwd.value, value.trim())
    ElMessage.success('已创建')
    await loadCwd(); reloadTree()
  } catch (e) { if (e !== 'cancel') ElMessage.error(e?.response?.data?.detail || '创建失败') }
}

async function doRename(row) {
  try {
    const { value } = await ElMessageBox.prompt('新名称', '重命名', {
      confirmButtonText: '确定', cancelButtonText: '取消', inputValue: row.name,
      inputPattern: /.+/, inputErrorMessage: '名称不能为空',
    })
    const name = value.trim()
    if (name === row.name) return
    await fm.rename(cwd.value, row.path, name)
    ElMessage.success('已重命名')
    await loadCwd(); if (row.is_dir) reloadTree()
  } catch (e) { if (e !== 'cancel') ElMessage.error(e?.response?.data?.detail || '重命名失败') }
}

async function doDelete(row) {
  try {
    await ElMessageBox.confirm(`确定删除「${row.name}」？${row.is_dir ? '（含其中全部内容）' : ''}`, '删除', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
      confirmButtonClass: 'el-button--danger',
    })
    await fm.remove(cwd.value, [row.path])
    ElMessage.success('已删除')
    if (selectedPath.value === row.path) { showInfo.value = false; selectedPath.value = '' }
    clearSel()
    await loadCwd(); if (row.is_dir) reloadTree()
  } catch (e) { if (e !== 'cancel') ElMessage.error(e?.response?.data?.detail || '删除失败') }
}

async function doDeleteMany(rows) {
  try {
    await ElMessageBox.confirm(`确定删除选中的 ${rows.length} 项？（文件夹含其中全部内容）`, '批量删除', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
      confirmButtonClass: 'el-button--danger',
    })
    await fm.remove(cwd.value, rows.map((r) => r.path))
    ElMessage.success(`已删除 ${rows.length} 项`)
    if (rows.some((r) => r.path === selectedPath.value)) { showInfo.value = false; selectedPath.value = '' }
    const hadDir = rows.some((r) => r.is_dir)
    clearSel()
    await loadCwd(); if (hadDir) reloadTree()
  } catch (e) { if (e !== 'cancel') ElMessage.error(e?.response?.data?.detail || '删除失败') }
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
    await fm[destDlg.mode](cwd.value, dest, rows.map((r) => r.path))
    ElMessage.success(`已${verb} ${rows.length} 项`)
    destDlg.show = false
    if (rows.some((r) => r.path === selectedPath.value)) { showInfo.value = false; selectedPath.value = '' }
    clearSel()
    await loadCwd(); if (rows.some((r) => r.is_dir)) reloadTree()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || `${verb}失败`)
  } finally {
    destDlg.busy = false
  }
}

// ---------- 拖动移动（把文件/文件夹拖到目标文件夹或左侧目录树）----------
function parentOf(p) { return p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '' }
// 拖的是已选中集合里的项 → 拖整个选中集；否则只拖这一行
function dragSetFor(row) {
  return selPaths.value.length > 1 && selPaths.value.includes(row.path) ? selRows.value : [row]
}
// dest 是否是合法落点：非自身/子孙、且不是原地（源就在该目录下）
function canDropDest(dest) {
  if (!dnd.dragging || dest === null) return false
  return !dnd.rows.some(
    (r) => r.path === dest || parentOf(r.path) === dest || (r.is_dir && dest.startsWith(r.path + '/')),
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
function onDragEndRow() { dnd.dragging = false; dnd.rows = []; dnd.overPath = null }
function onDestDragOver(ev, dest) {
  if (!canDropDest(dest)) return
  ev.preventDefault()
  ev.stopPropagation() // 别冒泡到 fm-main 的上传拖放
  ev.dataTransfer.dropEffect = 'move'
  dnd.overPath = dest
}
function onDestDragLeave(dest) { if (dnd.overPath === dest) dnd.overPath = null }
async function onDestDrop(ev, dest) {
  if (!canDropDest(dest)) return
  ev.preventDefault()
  ev.stopPropagation()
  const rows = dnd.rows.slice()
  onDragEndRow()
  await moveInto(dest, rows)
}
// 行/卡片作落点时，仅文件夹可接收
function onRowDragOver(ev, row) { if (row.is_dir) onDestDragOver(ev, row.path) }
function onRowDragLeave(row) { if (row.is_dir) onDestDragLeave(row.path) }
function onRowDrop(ev, row) { if (row.is_dir) onDestDrop(ev, row.path) }
async function moveInto(dest, rows) {
  const movable = rows.filter(
    (r) => parentOf(r.path) !== dest && r.path !== dest && !(r.is_dir && dest.startsWith(r.path + '/')),
  )
  if (!movable.length) return
  try {
    await fm.move(cwd.value, dest, movable.map((r) => r.path))
    ElMessage.success(`已移动 ${movable.length} 项`)
    if (movable.some((r) => r.path === selectedPath.value)) { showInfo.value = false; selectedPath.value = '' }
    clearSel()
    await loadCwd(); if (movable.some((r) => r.is_dir)) reloadTree()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '移动失败')
  }
}

// ---------- 框选多选（在空白处按下并拖动画选择框）----------
function onContentMousedown(ev) {
  if (ev.button !== 0) return
  if (isMobile.value) return // 移动端不启动框选，改用「选择」模式
  // 落在行/卡片/表头/控件上时不启动框选，交给点击或拖拽
  if (ev.target.closest(
    '.el-table__row, .cell, .el-table__header, .rowmore, .el-checkbox, .el-dropdown, a, button, input',
  )) return
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
    marquee.x0 = sx; marquee.y0 = sy; marquee.x1 = e.clientX; marquee.y1 = e.clientY
    applyMarquee()
  }
  const onUp = () => {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    document.body.style.userSelect = ''
    if (moved) { marquee.show = false; marqueeSuppressBlank = true }
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
    if (b.right >= L && b.left <= R && b.bottom >= T && b.top <= B && rows[i]) hit.add(rows[i].path)
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
function openCtxBlank(ev) { openCtx(ev, []) }
function onTableRowCtx(row, _col, ev) { openCtxRow(ev, row) }
function closeCtx() { ctx.show = false }
function runCtx(item) { closeCtx(); item.run() }
function onGlobalKey(e) { if (e.key === 'Escape') closeCtx() }

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
    items.push({ label: '删除', icon: Delete, danger: true, divided: true, run: () => doDelete(r) })
    return items
  }
  const files = rows.filter((r) => !r.is_dir)
  return [
    ...(files.length
      ? [{ label: `下载 ${files.length} 个文件`, icon: Download, run: () => doDownloadMany(rows) }]
      : []),
    { label: `移动 ${rows.length} 项到…`, icon: Right, run: () => startMoveCopy('move', rows) },
    { label: `复制 ${rows.length} 项到…`, icon: CopyDocument, run: () => startMoveCopy('copy', rows) },
    { label: `删除 ${rows.length} 项`, icon: Delete, danger: true, divided: true, run: () => doDeleteMany(rows) },
  ]
})

// ---------- 上传（直传七牛，支持文件夹；并行 3 路） ----------
function pickFiles() { fileInput.value?.click() }
function pickFolder() { dirInput.value?.click() }
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
  await Promise.all(Array.from({ length: Math.min(conf.upload_concurrency, items.length) }, worker))
  if (failed) ElMessage.warning(`上传完成，${failed} 个失败（详见传输列表）`)
  else ElMessage.success(`上传完成（${items.length} 个）`)
  const hadFolder = list.some((x) => (x.name || '').includes('/'))
  if (cwd.value === dir) { await loadCwd(); if (hadFolder) reloadTree() }
  else if (hadFolder) reloadTree()
}

// ---------- 拖拽上传（保留文件夹结构） ----------
const dragDepth = ref(0)
const dragging = computed(() => dragDepth.value > 0)
function hasFiles(ev) { return [...(ev.dataTransfer?.types || [])].includes('Files') }
function onDragEnter(ev) { if (hasFiles(ev)) dragDepth.value++ }
function onDragLeave(ev) { if (hasFiles(ev) && dragDepth.value > 0) dragDepth.value-- }
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
function isImage(r) { return r.content_type?.startsWith('image/') }
function isText(r) {
  return /^text\//.test(r.content_type || '') || /\.(md|txt|yml|yaml|json|log|csv|xml|ini|conf)$/i.test(r.name)
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
function fmtSize(n) {
  if (!n) return '—'
  const u = ['B', 'KB', 'MB', 'GB']
  let i = 0, v = n
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`
}
function fmtDate(ms) {
  if (!ms) return '—'
  const d = new Date(ms)
  return `${d.getMonth() + 1}月${d.getDate()}日`
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
</script>

<template>
  <div class="fm">
    <div class="fm-card">
      <!-- 左侧：目录树 + 存储条（移动端隐藏，改用面包屑导航） -->
      <aside v-if="!isMobile" class="fm-side" :style="{ width: sideW + 'px' }">
        <el-dropdown trigger="click" @command="(c) => (c === 'file' ? pickFiles() : pickFolder())">
          <el-button type="primary" class="fm-upload" :icon="Upload">上传</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="file" :icon="Files">上传文件</el-dropdown-item>
              <el-dropdown-item command="folder" :icon="FolderOpened">上传文件夹</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <input ref="fileInput" type="file" multiple hidden @change="onFilesPicked" />
        <input ref="dirInput" type="file" multiple webkitdirectory hidden @change="onFilesPicked" />

        <div class="fm-tree">
          <el-tree
            :key="treeKey"
            ref="treeRef"
            lazy
            :load="loadTreeNode"
            :props="treeProps"
            node-key="path"
            :current-node-key="cwd"
            :expand-on-click-node="false"
            :default-expanded-keys="['']"
            highlight-current
            @node-click="onTreeClick"
          >
            <template #default="{ node }">
              <span
                class="tnode"
                :class="{ 'drop-into': dnd.dragging && dnd.overPath === node.data.path }"
                @dragover="onDestDragOver($event, node.data.path)"
                @dragleave="onDestDragLeave(node.data.path)"
                @drop="onDestDrop($event, node.data.path)"
              >
                <el-icon class="ti"><FolderOpened v-if="node.expanded" /><Folder v-else /></el-icon>
                <span class="tlabel">{{ node.label }}</span>
              </span>
            </template>
          </el-tree>
        </div>

        <div class="fm-drive">
          <el-progress :percentage="drivePct" :stroke-width="6" :show-text="false" />
          <div class="fm-drive-t">
            <template v-if="drive">
              已用 {{ drive.used ? fmtSize(drive.used) : '0 B' }} / {{ fmtSize(drive.quota) }}
              · {{ drive.files }} 个文件
            </template>
            <template v-else>用量统计加载中…</template>
          </div>
        </div>
      </aside>

      <div v-if="!isMobile" class="fm-rs" @mousedown.prevent="startResize('side', $event)"></div>

      <!-- 中间：工具栏 + 面包屑 + 内容（整块可拖入上传） -->
      <section
        class="fm-main"
        @dragenter.prevent="onDragEnter"
        @dragover.prevent
        @dragleave="onDragLeave"
        @drop.prevent="onDrop"
      >
        <div class="fm-toolbar">
          <div class="fm-tb-left">
            <template v-if="selPaths.length">
              <span class="fm-selinfo">已选 {{ selPaths.length }} 项</span>
              <!-- 桌面：批量操作内联；移动端移到底部操作条，这里只留计数 -->
              <template v-if="!isMobile">
                <el-button text :icon="Download" :disabled="!selFiles.length" @click="doDownloadMany(selRows)">下载</el-button>
                <el-button text :icon="Right" @click="startMoveCopy('move', selRows)">移动</el-button>
                <el-button text :icon="CopyDocument" @click="startMoveCopy('copy', selRows)">复制</el-button>
                <el-button text type="danger" :icon="Delete" @click="doDeleteMany(selRows)">删除</el-button>
                <el-button text :icon="Close" @click="clearSel">取消选择</el-button>
              </template>
            </template>
            <template v-else-if="!isMobile">
              <el-button :icon="FolderAdd" text @click="newFolder">新建文件夹</el-button>
            </template>
          </div>
          <div class="fm-tb-right">
            <!-- 搜索框：桌面常驻；移动端仅在展开时占整行（默认收起，走右下角搜索按钮） -->
            <el-input
              v-if="!isMobile || (searchOpen && !selectMode)"
              ref="searchInputRef"
              v-model="q"
              :prefix-icon="Search"
              placeholder="搜索当前目录"
              clearable
              class="fm-search"
            />
            <el-button v-if="isMobile && searchOpen && !selectMode" text class="fm-search-cancel" @click="closeSearch">取消</el-button>
            <template v-if="!isMobile">
              <el-segmented v-model="viewMode" :options="viewOptions" class="fm-seg">
                <template #default="{ item }"><el-icon><component :is="item.icon" /></el-icon></template>
              </el-segmented>
              <el-button :icon="View" circle :type="showInfo ? 'primary' : ''" :plain="showInfo" title="预览面板" @click="showInfo = !showInfo" />
            </template>
            <el-button
              v-else-if="!searchOpen"
              :icon="selectMode ? Close : Select"
              circle
              :type="selectMode ? 'primary' : ''"
              :plain="selectMode"
              title="多选"
              @click="toggleSelectMode"
            />
          </div>
        </div>

        <div class="fm-crumb">
          <el-breadcrumb separator="/">
            <el-breadcrumb-item v-for="c in crumbs" :key="c.path">
              <a class="crumb" @click="goto(c.path)">{{ c.name }}</a>
            </el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <div
          v-loading="loading"
          ref="contentEl"
          class="fm-content"
          @contextmenu="openCtxBlank"
          @click="onBlankClick"
          @mousedown="onContentMousedown"
        >
          <!-- 列表 -->
          <el-table
            v-if="effectiveView === 'list'"
            ref="tableRef"
            :data="filtered"
            row-key="path"
            :row-class-name="rowClass"
            @row-click="onRowClick"
            @row-dblclick="onRowDblclick"
            @row-contextmenu="onTableRowCtx"
            @selection-change="onSelChange"
          >
            <el-table-column type="selection" width="40" :reserve-selection="false" />
            <el-table-column label="名称" min-width="260">
              <template #default="{ row }">
                <span
                  class="namecell"
                  :class="{ 'drop-into': row.is_dir && dnd.overPath === row.path }"
                  draggable="true"
                  @dragstart="onDragStartRow($event, row)"
                  @dragend="onDragEndRow"
                  @dragover="onRowDragOver($event, row)"
                  @dragleave="onRowDragLeave(row)"
                  @drop="onRowDrop($event, row)"
                >
                  <el-icon class="nc-ico" :style="{ color: tintOf(row) }"><component :is="iconOf(row)" /></el-icon>
                  <span class="nc-name">{{ row.name }}</span>
                </span>
              </template>
            </el-table-column>
            <el-table-column label="大小" width="120" align="right">
              <template #default="{ row }"><span class="muted">{{ row.is_dir ? '—' : fmtSize(row.size) }}</span></template>
            </el-table-column>
            <el-table-column label="修改时间" width="130">
              <template #default="{ row }"><span class="muted">{{ fmtDate(row.updated_at) }}</span></template>
            </el-table-column>
            <el-table-column width="56" align="center">
              <template #default="{ row }">
                <el-dropdown trigger="click" @command="(c) => onRowCommand(c, row)">
                  <el-icon class="rowmore" @click.stop><MoreFilled /></el-icon>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item :icon="Download" command="下载" :disabled="row.is_dir">下载</el-dropdown-item>
                      <el-dropdown-item :icon="EditPen" command="重命名">重命名</el-dropdown-item>
                      <el-dropdown-item :icon="Delete" command="删除" divided>删除</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </template>
            </el-table-column>
            <template #empty><el-empty description="空目录，可直接拖文件进来" :image-size="80" /></template>
          </el-table>

          <!-- 网格 -->
          <div v-else class="fm-grid">
            <div
              v-for="row in filtered"
              :key="row.path"
              class="cell"
              :class="{
                'is-sel': row.path === selectedPath,
                'is-checked': selPaths.includes(row.path),
                'drop-into': row.is_dir && dnd.overPath === row.path,
              }"
              :draggable="!isMobile"
              @click="onCellTap(row, $event)"
              @dblclick="onRowDblclick(row)"
              @contextmenu="openCtxRow($event, row)"
              @dragstart="onDragStartRow($event, row)"
              @dragend="onDragEndRow"
              @dragover="onRowDragOver($event, row)"
              @dragleave="onRowDragLeave(row)"
              @drop="onRowDrop($event, row)"
            >
              <button v-if="isMobile && !selectMode" class="cell-more" @click.stop="openSheet(row)">
                <el-icon><MoreFilled /></el-icon>
              </button>
              <el-icon
                v-if="isMobile && selectMode"
                class="cell-check"
                :class="{ on: selPaths.includes(row.path) }"
              ><Select /></el-icon>
              <div class="cell-thumb">
                <img v-if="isImage(row)" :src="fm.previewUrl(row.path, { w: 320, h: 200, mode: 'fill' })" class="cell-img" loading="lazy" alt="" />
                <el-icon v-else class="cell-ico" :style="{ color: tintOf(row) }"><component :is="iconOf(row)" /></el-icon>
              </div>
              <div class="cell-name">{{ row.name }}</div>
            </div>
            <el-empty v-if="!filtered.length && !loading" description="空目录，可直接拖文件进来" :image-size="80" class="grid-empty" />
          </div>
        </div>

        <!-- 拖入蒙层 -->
        <div v-if="dragging" class="fm-drop">
          <el-icon :size="36"><Upload /></el-icon>
          <span>释放以上传到「{{ crumbs[crumbs.length - 1].name }}」</span>
        </div>
      </section>

      <!-- 右侧：预览 + 信息（移动端改用底部弹层，不渲染此面板） -->
      <template v-if="showInfo && !isMobile">
        <div class="fm-rs" @mousedown.prevent="startResize('info', $event)"></div>
        <aside class="fm-info" :style="{ width: infoW + 'px' }">
          <div class="fm-info-head">
            <span class="fm-info-title">{{ selected ? selected.name : '预览' }}</span>
            <el-icon class="fm-info-close" title="收起" @click="showInfo = false"><Close /></el-icon>
          </div>
          <template v-if="selected && !selected.is_dir">
            <div class="fm-preview">
              <div v-if="isImage(selected)" class="pv-img-wrap" title="点击放大" @click="openImgViewer(selected)">
                <img :src="fm.previewUrl(selected.path, { w: 900, mode: 'fit' })" class="pv-img" alt="" />
              </div>
              <pre v-else-if="isText(selected)" class="pv-text">{{ previewErr || previewText || '加载中…' }}</pre>
              <div v-else class="pv-file">
                <el-icon :size="56" :style="{ color: tintOf(selected) }"><component :is="iconOf(selected)" /></el-icon>
              </div>
            </div>
            <el-descriptions :column="1" size="small" border class="fm-desc">
              <el-descriptions-item label="类型">{{ selected.content_type || '文件' }}</el-descriptions-item>
              <el-descriptions-item label="大小">{{ fmtSize(selected.size) }}</el-descriptions-item>
              <el-descriptions-item label="修改时间">{{ fmtDate(selected.updated_at) }}</el-descriptions-item>
            </el-descriptions>
            <div class="fm-info-actions">
              <el-button :icon="Download" @click="doDownload(selected)">下载</el-button>
              <el-button :icon="EditPen" @click="doRename(selected)">重命名</el-button>
            </div>
          </template>
          <el-empty v-else description="选择一个文件查看预览" :image-size="90" />
        </aside>
      </template>
    </div>

    <!-- 双击的文本预览/编辑弹窗（图片走 el-image-viewer） -->
    <el-dialog
      v-model="modal.show"
      :title="modal.row?.name"
      :width="isMobile ? '94%' : '72%'"
      :top="isMobile ? '4vh' : '6vh'"
      append-to-body
      :before-close="beforeCloseModal"
    >
      <div v-if="modal.row" class="fm-modal-body">
        <template v-if="isText(modal.row)">
          <el-input
            v-if="modal.editing"
            v-model="modal.draft"
            type="textarea"
            :rows="22"
            resize="none"
            class="fm-modal-edit"
          />
          <pre v-else class="fm-modal-text">{{ modal.err || modal.text || '加载中…' }}</pre>
        </template>
        <div v-else class="fm-modal-file">
          <el-icon :size="72" :style="{ color: tintOf(modal.row) }"><component :is="iconOf(modal.row)" /></el-icon>
          <span class="fm-modal-hint">该类型暂不支持在线预览</span>
          <el-button type="primary" :icon="Download" @click="doDownload(modal.row)">下载</el-button>
        </div>
      </div>
      <template v-if="modal.row && isText(modal.row)" #footer>
        <template v-if="modal.editing">
          <el-button @click="modal.editing = false">取消</el-button>
          <el-button type="primary" :loading="modal.saving" @click="saveEdit">保存</el-button>
        </template>
        <template v-else>
          <el-button :icon="Download" @click="doDownload(modal.row)">下载</el-button>
          <el-button type="primary" :icon="EditPen" :disabled="!modal.loaded" @click="startEdit">编辑</el-button>
        </template>
      </template>
    </el-dialog>

    <!-- 图片查看器：滚轮/工具栏缩放、旋转 -->
    <el-image-viewer
      v-if="imgViewer.show"
      :url-list="imgViewer.urls"
      hide-on-click-modal
      teleported
      @close="imgViewer.show = false"
    />

    <!-- 传输列表：上传/下载进度（右下角浮层） -->
    <div v-if="tasks.length" class="fm-tasks">
      <div class="fm-tasks-head" @click="tasksCollapsed = !tasksCollapsed">
        <span class="fm-tasks-title">
          <el-icon v-if="activeCount" class="spin"><Loading /></el-icon>
          传输列表{{ activeCount ? `（${activeCount} 进行中）` : '' }}
        </span>
        <span class="fm-tasks-ops">
          <el-icon v-if="!activeCount" title="清除记录" @click.stop="tasks = []"><Delete /></el-icon>
          <el-icon><ArrowDown v-if="!tasksCollapsed" /><ArrowUp v-else /></el-icon>
        </span>
      </div>
      <div v-show="!tasksCollapsed" class="fm-tasks-body">
        <div v-for="t in tasks" :key="t.id" class="fm-task">
          <el-icon class="ft-kind"><Upload v-if="t.kind === 'up'" /><Download v-else /></el-icon>
          <div class="ft-main">
            <div class="ft-name">{{ t.name }}</div>
            <el-progress
              :percentage="t.pct"
              :stroke-width="4"
              :show-text="false"
              :status="t.status === 'error' ? 'exception' : t.status === 'done' ? 'success' : undefined"
            />
            <div v-if="t.err" class="ft-err">{{ t.err }}</div>
          </div>
          <span class="ft-pct" :class="t.status">
            {{ t.status === 'done' ? '完成' : t.status === 'error' ? '失败' : `${t.pct}%` }}
          </span>
        </div>
      </div>
    </div>

    <!-- 右键菜单（teleport 到 body，不用 CSS 变量以免脱离 .fm 作用域后失效） -->
    <Teleport to="body">
      <div v-if="ctx.show" class="fm-ctx" :style="{ left: ctx.x + 'px', top: ctx.y + 'px' }" @contextmenu.prevent @click.stop>
        <template v-for="(item, i) in ctxItems" :key="i">
          <div v-if="item.divided" class="fm-ctx-divider"></div>
          <div class="fm-ctx-item" :class="{ danger: item.danger }" @click="runCtx(item)">
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
          </div>
        </template>
      </div>
    </Teleport>

    <!-- 移动/复制目标目录选择 -->
    <el-dialog
      v-model="destDlg.show"
      :title="`${destDlg.mode === 'move' ? '移动' : '复制'} ${destDlg.rows.length} 项到…`"
      :width="isMobile ? '92%' : '420px'"
      append-to-body
    >
      <div class="dest-tree">
        <el-tree
          :key="`dest-${treeKey}-${destDlg.show}`"
          lazy
          :load="loadTreeNode"
          :props="treeProps"
          node-key="path"
          :expand-on-click-node="false"
          :default-expanded-keys="['']"
          highlight-current
          @node-click="(d) => (destDlg.path = d.path)"
        >
          <template #default="{ node }">
            <span class="tnode">
              <el-icon class="ti"><FolderOpened v-if="node.expanded" /><Folder v-else /></el-icon>
              <span class="tlabel">{{ node.label }}</span>
            </span>
          </template>
        </el-tree>
      </div>
      <template #footer>
        <el-button @click="destDlg.show = false">取消</el-button>
        <el-button type="primary" :disabled="destDlg.path === null" :loading="destDlg.busy" @click="confirmDest">
          {{ destDlg.mode === 'move' ? '移动到此' : '复制到此' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 框选选择框（视口固定坐标） -->
    <div v-if="marquee.show" class="fm-marquee" :style="marqueeStyle"></div>

    <!-- 移动端：右下角搜索按钮（点开顶部搜索栏） -->
    <el-button
      v-if="isMobile && !selectMode && !searchOpen"
      circle
      :icon="Search"
      class="fm-fab-search"
      title="搜索"
      @click="openSearch"
    />

    <!-- 移动端：悬浮 +（上传 / 新建文件夹） -->
    <el-dropdown
      v-if="isMobile && !selectMode"
      trigger="click"
      class="fm-fab"
      placement="top-end"
      @command="(c) => (c === 'file' ? pickFiles() : c === 'folder' ? pickFolder() : newFolder())"
    >
      <el-button type="primary" circle :icon="Plus" class="fm-fab-btn" />
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item command="file" :icon="Files">上传文件</el-dropdown-item>
          <el-dropdown-item command="folder" :icon="FolderOpened">上传文件夹</el-dropdown-item>
          <el-dropdown-item command="newfolder" :icon="FolderAdd" divided>新建文件夹</el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>

    <!-- 移动端：多选态底部批量操作条 -->
    <div v-if="isMobile && selectMode && selPaths.length" class="fm-selbar">
      <button class="fm-selbar-btn" :disabled="!selFiles.length" @click="doDownloadMany(selRows)">
        <el-icon><Download /></el-icon><span>下载</span>
      </button>
      <button class="fm-selbar-btn" @click="startMoveCopy('move', selRows)">
        <el-icon><Right /></el-icon><span>移动</span>
      </button>
      <button class="fm-selbar-btn" @click="startMoveCopy('copy', selRows)">
        <el-icon><CopyDocument /></el-icon><span>复制</span>
      </button>
      <button class="fm-selbar-btn danger" @click="doDeleteMany(selRows)">
        <el-icon><Delete /></el-icon><span>删除</span>
      </button>
    </div>

    <!-- 移动端：底部操作弹层 -->
    <el-drawer v-model="sheet.show" direction="btt" size="auto" :with-header="false" class="fm-sheet">
      <div v-if="sheet.row" class="fm-sheet-inner">
        <div class="fm-sheet-head">
          <el-icon class="fm-sheet-ico" :style="{ color: tintOf(sheet.row) }">
            <component :is="iconOf(sheet.row)" />
          </el-icon>
          <div class="fm-sheet-name">{{ sheet.row.name }}</div>
        </div>
        <div class="fm-sheet-grid">
          <button v-if="!sheet.row.is_dir" class="fm-sheet-btn" @click="sheetDo('preview')">
            <el-icon><View /></el-icon><span>预览</span>
          </button>
          <button v-if="!sheet.row.is_dir" class="fm-sheet-btn" @click="sheetDo('download')">
            <el-icon><Download /></el-icon><span>下载</span>
          </button>
          <button class="fm-sheet-btn" @click="sheetDo('rename')">
            <el-icon><EditPen /></el-icon><span>重命名</span>
          </button>
          <button class="fm-sheet-btn" @click="sheetDo('move')">
            <el-icon><Right /></el-icon><span>移动</span>
          </button>
          <button class="fm-sheet-btn" @click="sheetDo('copy')">
            <el-icon><CopyDocument /></el-icon><span>复制</span>
          </button>
          <button class="fm-sheet-btn danger" @click="sheetDo('delete')">
            <el-icon><Delete /></el-icon><span>删除</span>
          </button>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
/* ---------- Willow 皮：token ---------- */
.fm {
  --w-border: #ededf1;
  --w-border-soft: #f2f2f5;
  --w-side-bg: #fbfbfc;
  --w-sel: #eaedf5;
  --w-muted: #9fa1ae;
  --w-shadow: 0 1px 2px rgba(44, 47, 60, 0.06), 0 3px 10px rgba(44, 47, 60, 0.1);

  flex: 1;
  min-height: 0;
  display: flex;
  position: relative; /* 传输列表浮层的定位基准 */
}
.fm-card {
  flex: 1;
  min-height: 0;
  display: flex;
  background: #fff;
  border: 1px solid var(--w-border);
  border-radius: 12px;
  box-shadow: var(--w-shadow);
  overflow: hidden;
}

/* ---------- 左侧 ---------- */
.fm-side {
  flex-shrink: 0;
  border-right: 1px solid var(--w-border);
  background: var(--w-side-bg);
  display: flex;
  flex-direction: column;
  padding: 14px 12px;
  gap: 12px;
}
.fm-upload { width: 100%; }
.fm-tree { flex: 1; min-height: 0; overflow: auto; }
.fm-tree :deep(.el-tree) { background: transparent; }
.fm-tree :deep(.el-tree-node__content) { height: 34px; border-radius: 8px; }
.tnode { display: inline-flex; align-items: center; gap: 8px; font-size: 0.9rem; }
.ti { color: #e8a13a; }
.fm-drive { border-top: 1px solid var(--w-border); padding-top: 12px; }
.fm-drive-t { font-size: 12px; color: var(--w-muted); margin-top: 7px; }

/* ---------- 拖拽分隔条 ---------- */
.fm-rs {
  width: 5px;
  margin: 0 -2.5px;
  flex-shrink: 0;
  cursor: col-resize;
  z-index: 3;
  transition: background 0.15s;
}
.fm-rs:hover,
.fm-rs:active { background: var(--el-color-primary-light-7); }

/* ---------- 中间 ---------- */
.fm-main { flex: 1; min-width: 0; display: flex; flex-direction: column; position: relative; }
.fm-toolbar {
  height: 56px;
  flex-shrink: 0;
  border-bottom: 1px solid var(--w-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 14px;
  gap: 12px;
}
.fm-tb-left { display: flex; align-items: center; gap: 4px; min-width: 0; }
.fm-selinfo { font-size: 0.88rem; color: var(--el-text-color-primary); font-weight: 600; margin-right: 6px; white-space: nowrap; }
.spin { animation: fm-spin 1s linear infinite; }
@keyframes fm-spin { to { transform: rotate(360deg); } }
.fm-tb-right { display: flex; align-items: center; gap: 10px; }
.fm-search { width: 220px; }
.fm-crumb { padding: 11px 16px; border-bottom: 1px solid var(--w-border-soft); }
.crumb { cursor: pointer; color: var(--el-text-color-regular); }
.crumb:hover { color: var(--el-color-primary); }
.fm-content { flex: 1; min-height: 0; overflow: auto; padding: 6px 10px 10px; }

/* 拖入蒙层（pointer-events:none，drop 落在下层元素上冒泡到 fm-main） */
.fm-drop {
  position: absolute;
  inset: 0;
  z-index: 5;
  pointer-events: none;
  background: rgba(22, 119, 255, 0.06);
  border: 2px dashed var(--el-color-primary);
  border-radius: 0 12px 12px 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--el-color-primary);
  font-size: 0.95rem;
  font-weight: 600;
}

/* 拖动移动：落点文件夹高亮 */
.namecell.drop-into,
.tnode.drop-into {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 1px;
  border-radius: 6px;
  background: var(--el-color-primary-light-9);
}
.cell.drop-into {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 2px var(--el-color-primary) inset;
  background: var(--el-color-primary-light-9);
}
.tnode { border-radius: 6px; }

/* 框选选择框 */
.fm-marquee {
  position: fixed;
  z-index: 2500;
  pointer-events: none;
  border: 1px solid var(--el-color-primary);
  background: rgba(22, 119, 255, 0.1);
  border-radius: 2px;
}

/* 列表 */
.namecell { display: inline-flex; align-items: center; gap: 10px; cursor: grab; }
.nc-ico { font-size: 18px; }
.nc-name { color: var(--el-text-color-primary); }
.muted { color: var(--w-muted); font-size: 0.86rem; }
.rowmore { cursor: pointer; color: var(--w-muted); padding: 4px; border-radius: 6px; }
.rowmore:hover { background: #f0f2f5; color: var(--el-text-color-primary); }
.fm-content :deep(.el-table__row) { cursor: pointer; }
.fm-content :deep(.el-table__row.is-sel > td) { background: var(--w-sel); }
.fm-content :deep(.el-table__row.is-sel:hover > td) { background: var(--w-sel); }

/* 网格 */
.fm-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(132px, 1fr));
  gap: 12px;
  padding: 12px 8px;
}
.cell {
  position: relative;
  border: 1px solid var(--w-border);
  border-radius: 10px;
  padding: 12px;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}
/* 移动端网格卡片右上角 ⋮ 操作入口 */
.cell-more {
  position: absolute;
  top: 4px;
  right: 4px;
  z-index: 2;
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.82);
  color: #606266;
  cursor: pointer;
}
/* 选择模式的勾选标 */
.cell-check {
  position: absolute;
  top: 6px;
  left: 6px;
  z-index: 2;
  font-size: 18px;
  color: #c0c4cc;
  background: #fff;
  border-radius: 50%;
}
.cell-check.on {
  color: var(--el-color-primary);
}
.cell:hover { box-shadow: var(--w-shadow); }
.cell.is-sel { background: var(--w-sel); border-color: #d5d9e6; }
.cell.is-checked { border-color: var(--el-color-primary); box-shadow: 0 0 0 1px var(--el-color-primary) inset; }
.cell-thumb {
  height: 78px;
  border-radius: 8px;
  background: #f4f5f8;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 10px;
  overflow: hidden;
}
.cell-img { width: 100%; height: 100%; object-fit: cover; }
.cell-ico { font-size: 34px; }
.cell-name {
  font-size: 0.85rem;
  text-align: center;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.grid-empty { grid-column: 1 / -1; }

/* ---------- 右侧 ---------- */
.fm-info {
  flex-shrink: 0;
  border-left: 1px solid var(--w-border);
  background: var(--w-side-bg);
  padding: 16px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.fm-info-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.fm-info-title {
  font-weight: 600;
  font-size: 0.92rem;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fm-info-close { cursor: pointer; color: var(--w-muted); padding: 4px; border-radius: 6px; flex-shrink: 0; }
.fm-info-close:hover { background: #f0f2f5; color: var(--el-text-color-primary); }
/* 预览占大头：撑满面板剩余高度，信息/操作固定在底部 */
.fm-preview { flex: 1; min-height: 200px; display: flex; border-radius: 10px; overflow: hidden; }
.pv-img-wrap {
  flex: 1;
  min-height: 0;
  border: 1px solid var(--w-border);
  border-radius: 10px;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  cursor: zoom-in;
}
.pv-img { max-width: 100%; max-height: 100%; object-fit: contain; }
.pv-text {
  margin: 0;
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: #fff;
  border: 1px solid var(--w-border);
  border-radius: 10px;
  padding: 12px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-regular);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: pre-wrap;
  word-break: break-word;
}
.pv-file {
  flex: 1;
  min-height: 0;
  border: 1px solid var(--w-border);
  border-radius: 10px;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}
.fm-desc { flex-shrink: 0; }
.fm-desc :deep(.el-descriptions__label) { color: var(--w-muted); width: 72px; }
.fm-info-actions { display: flex; gap: 8px; flex-shrink: 0; }
.fm-info-actions .el-button { flex: 1; }

/* ---------- 双击预览弹窗 ---------- */
.fm-modal-body { display: flex; align-items: center; justify-content: center; min-height: 200px; }
.fm-modal-edit { width: 100%; }
.fm-modal-edit :deep(.el-textarea__inner) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  line-height: 1.7;
  max-height: 72vh;
}
.fm-modal-text {
  margin: 0;
  width: 100%;
  max-height: 72vh;
  overflow: auto;
  background: #fafbfc;
  border: 1px solid #ededf1;
  border-radius: 10px;
  padding: 14px 16px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--el-text-color-regular);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: pre-wrap;
  word-break: break-word;
}
.fm-modal-file {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 28px 0;
}
.fm-modal-hint { color: #9fa1ae; font-size: 0.9rem; }

/* ---------- 传输列表（右下角浮层） ---------- */
.fm-tasks {
  position: absolute;
  right: 18px;
  bottom: 18px;
  z-index: 20;
  width: 320px;
  background: #fff;
  border: 1px solid var(--w-border);
  border-radius: 12px;
  box-shadow: var(--w-shadow);
  overflow: hidden;
}
.fm-tasks-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  cursor: pointer;
  background: var(--w-side-bg);
  border-bottom: 1px solid var(--w-border);
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.fm-tasks-title { display: inline-flex; align-items: center; gap: 7px; }
.fm-tasks-ops { display: inline-flex; align-items: center; gap: 8px; color: var(--w-muted); }
.fm-tasks-ops .el-icon { cursor: pointer; padding: 3px; border-radius: 5px; }
.fm-tasks-ops .el-icon:hover { background: #f0f2f5; color: var(--el-text-color-primary); }
.fm-tasks-body { max-height: 260px; overflow: auto; padding: 6px 0; }
.fm-task {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 14px;
}
.ft-kind { color: var(--w-muted); flex-shrink: 0; }
.ft-main { flex: 1; min-width: 0; }
.ft-name {
  font-size: 0.82rem;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-bottom: 4px;
}
.ft-err { font-size: 0.76rem; color: var(--el-color-danger); margin-top: 3px; }
.ft-pct { font-size: 0.78rem; color: var(--w-muted); flex-shrink: 0; width: 38px; text-align: right; }
.ft-pct.done { color: var(--el-color-success); }
.ft-pct.error { color: var(--el-color-danger); }

/* ---------- 右键菜单（teleport 到 body，颜色写死不依赖 .fm 的变量） ---------- */
.fm-ctx {
  position: fixed;
  z-index: 3000;
  min-width: 172px;
  background: #fff;
  border: 1px solid #ededf1;
  border-radius: 10px;
  box-shadow: 0 1px 2px rgba(44, 47, 60, 0.06), 0 3px 10px rgba(44, 47, 60, 0.12);
  padding: 6px;
}
.fm-ctx-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 7px 10px;
  border-radius: 6px;
  font-size: 0.88rem;
  cursor: pointer;
  color: var(--el-text-color-primary);
  white-space: nowrap;
}
.fm-ctx-item:hover { background: #f0f2f5; }
.fm-ctx-item.danger { color: var(--el-color-danger); }
.fm-ctx-item .el-icon { color: #9fa1ae; }
.fm-ctx-item.danger .el-icon { color: var(--el-color-danger); }
.fm-ctx-divider { height: 1px; background: #ededf1; margin: 5px 4px; }

/* ---------- 目标目录弹窗 ---------- */
.dest-tree {
  max-height: 320px;
  overflow: auto;
  border: 1px solid #ededf1;
  border-radius: 10px;
  padding: 8px;
}
.dest-tree :deep(.el-tree-node__content) { height: 32px; border-radius: 6px; }

/* 窄屏：收起右侧预览面板 */
@media (max-width: 900px) {
  .fm-side { width: 180px !important; }
  .fm-info,
  .fm-rs { display: none; }
}

/* ---------- 移动端悬浮 + 按钮 ---------- */
.fm-fab {
  position: fixed;
  right: 18px;
  bottom: 22px;
  z-index: 1500;
}
.fm-fab-btn {
  width: 52px;
  height: 52px;
  font-size: 22px;
  box-shadow: 0 6px 18px rgba(22, 119, 255, 0.38);
}
/* 搜索按钮：叠在 + 之上，白底次要样式 */
.fm-fab-search {
  position: fixed;
  right: 21px;
  bottom: 84px;
  z-index: 1500;
  width: 46px;
  height: 46px;
  font-size: 18px;
  background: #fff;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.16);
}

/* ---------- 移动端多选底部批量操作条 ---------- */
.fm-selbar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1400;
  display: flex;
  background: #fff;
  border-top: 1px solid var(--w-border);
  box-shadow: 0 -2px 14px rgba(0, 0, 0, 0.07);
  padding: 6px 4px calc(6px + env(safe-area-inset-bottom, 0px));
}
.fm-selbar-btn {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 8px 2px;
  border: none;
  background: none;
  color: #3c4149;
  font-size: 0.74rem;
  cursor: pointer;
}
.fm-selbar-btn .el-icon { font-size: 20px; }
.fm-selbar-btn.danger { color: var(--el-color-danger); }
.fm-selbar-btn:disabled { opacity: 0.38; }

/* ---------- 移动端底部操作弹层 ---------- */
.fm-sheet :deep(.el-drawer__body) { padding: 0; }
.fm-sheet-inner { padding: 8px 12px 20px; }
.fm-sheet-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 6px 14px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.fm-sheet-ico { font-size: 26px; flex-shrink: 0; }
.fm-sheet-name {
  font-weight: 600;
  font-size: 0.95rem;
  word-break: break-all;
}
.fm-sheet-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  padding-top: 14px;
}
.fm-sheet-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 7px;
  padding: 14px 6px;
  border: none;
  border-radius: 12px;
  background: #f5f7fa;
  color: #3c4149;
  font-size: 0.8rem;
  cursor: pointer;
}
.fm-sheet-btn .el-icon { font-size: 21px; }
.fm-sheet-btn.danger { color: var(--el-color-danger); }

/* ---------- 移动端整体布局（≤768） ---------- */
@media (max-width: 768px) {
  /* 工具栏单行：左计数 / 右搜索栏或多选开关；批量操作移到底部条 */
  .fm-toolbar { gap: 8px; padding: 0 10px; }
  .fm-tb-left { min-width: 0; }
  .fm-tb-right { flex: 1; justify-content: flex-end; gap: 8px; }
  .fm-search { flex: 1; }
  .fm-search-cancel { flex-shrink: 0; }
  .fm-content { padding: 6px; }
  .fm-grid {
    grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
    gap: 10px;
    padding: 10px 4px 80px; /* 底部留白避开 FAB / 操作条 */
  }
  .cell-thumb { height: 68px; }
  /* 传输列表：右下角浮窗 → 贴底通栏 */
  .fm-tasks {
    left: 8px;
    right: 8px;
    bottom: 8px;
    width: auto;
  }
}
</style>
