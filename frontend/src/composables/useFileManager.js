// 文件管理器领域逻辑：本文件是唯一入口，FilesLabView 只解构它的返回值 + 渲染。
// 本文件留「核心」——目录状态与加载、增删改、移动/复制弹窗、面板宽度、移动端弹层、
// 展示辅助；另外五块按职责拆进 fileManager/ 下，各自的输入/输出写在各文件头：
//   sel       fileManager/useSelection.js    选择 + 框选
//   preview   fileManager/usePreview.js      右侧预览面板 + 双击弹窗（含在线编辑）
//   transfers fileManager/useTransfers.js    传输列表 + 上传 + 下载
//   dnd       fileManager/useDragDrop.js     拖动移动 + 拖拽上传
//   ctxmenu   fileManager/useContextMenu.js  右键菜单
// 它们是内部实现，不对视图之外的地方开放；返回值按这五个名字分组，核心那部分仍是平铺。
//
// 跨模块共享的状态只有一份，由「谁最有资格拥有」决定归属，其余模块靠参数拿到同一个 ref：
//   core → cwd / entries / viewMode / showInfo / conf / 各 DOM ref / filtered
//   sel  → selectedPath / selPaths / selectMode（preview、dnd、ctxmenu 都要读写它们）
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { Folder, Document, Picture, Files, Grid, List } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { fmApi as fm, settingsApi } from '../api'
import { useIsMobile } from './useIsMobile'
import { fmtBytes, fmtMonthDay } from '../utils/format'
import { confirmDanger } from '../utils/confirm'
import { createName, renamePlan, FmNameError } from '../utils/fmPath'
import { useSelection } from './fileManager/useSelection'
import { usePreview } from './fileManager/usePreview'
import { useTransfers } from './fileManager/useTransfers'
import { useDragDrop } from './fileManager/useDragDrop'
import { useContextMenu } from './fileManager/useContextMenu'

export function useFileManager() {
  // ---------- 状态 ----------
  const cwd = ref('') // 当前目录相对路径，''=根
  const entries = ref([])
  const loading = ref(false)
  const q = ref('')
  const viewMode = ref('list') // list | grid
  const showInfo = ref(false) // 预览面板默认收起，点文件才弹出

  // ---------- 移动端 ----------
  const isMobile = useIsMobile()
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

  // 视图持有的 DOM / 组件句柄（模板里 ref="xxx" 要绑到顶层名字，故一律留在 core）
  const treeRef = ref(null)
  const treeKey = ref(0) // 目录变动时 bump 强制重挂树
  const tableRef = ref(null)
  const fileInput = ref(null)
  const dirInput = ref(null)
  const contentEl = ref(null)

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

  // ---------- 展示辅助（子模块也要用，先定义） ----------
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
  function textTooLarge(row) {
    return row.size > conf.text_preview_max_kb * 1024
  }

  // ---------- 子模块 ----------
  // 下面传进去的 loadCwd / goto / reloadTree 等都是函数声明，提升后可用；
  // 传 ref 而不是 .value，共享的就是同一份状态。
  const sel = useSelection({
    entries,
    filtered,
    viewMode,
    isMobile,
    tableRef,
    contentEl,
    showInfo,
  })
  const { selectedPath, selPaths, selRows, clearSel } = sel

  const preview = usePreview({
    fm,
    selectedPath,
    showInfo,
    conf,
    isImage,
    isText,
    textTooLarge,
    loadCwd,
  })

  const transfers = useTransfers({
    fm,
    conf,
    cwd,
    loadCwd,
    reloadTree,
    fileInput,
    dirInput,
  })

  const dnd = useDragDrop({
    fm,
    cwd,
    selPaths,
    selRows,
    selectedPath,
    showInfo,
    clearSel,
    loadCwd,
    reloadTree,
    uploadMany: transfers.uploadMany,
  })

  const ctxmenu = useContextMenu({
    selPaths,
    selRows,
    selectedPath,
    actions: {
      newFolder,
      pickFiles: transfers.pickFiles,
      pickFolder: transfers.pickFolder,
      loadCwd,
      goto,
      selectFile: preview.selectFile,
      doDownload: transfers.doDownload,
      doDownloadMany: transfers.doDownloadMany,
      doRename,
      startMoveCopy,
      doDelete,
      doDeleteMany,
    },
  })

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

  // ---------- 行为 ----------
  // 单击延迟执行、双击时取消：否则双击的第一下就弹出右侧预览面板，
  // 表格随之变窄、行发生位移，第二下会点到别处，双击时灵时不灵。
  let clickTimer = null
  function onRowClick(row) {
    clearTimeout(clickTimer)
    clickTimer = setTimeout(() => preview.selectFile(row), 250)
  }
  function onRowDblclick(row) {
    clearTimeout(clickTimer)
    row.is_dir ? goto(row.path) : preview.openModal(row)
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
    if (sel.selectMode.value) return sel.toggleCheck(row)
    if (row.is_dir) goto(row.path)
    else if (isImage(row)) preview.openImgViewer(row)
    else preview.openModal(row)
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
      if (isImage(row)) preview.openImgViewer(row)
      else preview.openModal(row)
    } else if (action === 'download') transfers.doDownload(row)
    else if (action === 'rename') doRename(row)
    else if (action === 'move') startMoveCopy('move', [row])
    else if (action === 'copy') startMoveCopy('copy', [row])
    else if (action === 'delete') doDelete(row)
  }

  async function newFolder() {
    try {
      const { value } = await ElMessageBox.prompt('文件夹名称', '新建文件夹', {
        confirmButtonText: '创建',
        cancelButtonText: '取消',
        inputPattern: /.+/,
        inputErrorMessage: '名称不能为空',
      })
      // 名称原样发（判词见 utils/fmPath.js）：这里不 trim，" a " 就是 " a "。
      await fm.createFolder(cwd.value, createName(value))
      ElMessage.success('已创建')
      await loadCwd()
      reloadTree()
    } catch (e) {
      // 三类都落这儿：取消（ElMessageBox reject 'cancel'/'close'，不提示）、
      // 名称非法（本地判词拦下的，后端没收到过，得自己提示）、
      // 请求失败（axios 拦截器已统一提示）。
      if (e instanceof FmNameError) ElMessage.error(e.message)
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
      // 名称原样发、identity 逐字比（判词见 utils/fmPath.js）。
      const plan = renamePlan(row.name, value)
      if (!plan.send) return // 名字逐字没变：不打后端
      await fm.rename(cwd.value, row.path, plan.name)
      ElMessage.success('已重命名')
      await loadCwd()
      if (row.is_dir) reloadTree()
    } catch (e) {
      // 同 newFolder：取消 / 名称非法 / 请求失败三类都落这儿。
      if (e instanceof FmNameError) ElMessage.error(e.message)
    }
  }

  async function doDelete(row) {
    const ok = await confirmDanger(
      `确定删除「${row.name}」？${row.is_dir ? '（含其中全部内容）' : ''}`,
      '删除',
    )
    if (!ok) return
    try {
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
      /* 失败已由 axios 拦截器统一提示 */
    }
  }

  async function doDeleteMany(rows) {
    const ok = await confirmDanger(
      `确定删除选中的 ${rows.length} 项？（文件夹含其中全部内容）`,
      '批量删除',
    )
    if (!ok) return
    try {
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
      /* 失败已由 axios 拦截器统一提示 */
    }
  }

  function onRowCommand(cmd, row) {
    if (cmd === '下载') transfers.doDownload(row)
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

  // ---------- 面板宽度拖拽 ----------
  // 拖拽中的收尾函数。正常在 mouseup 时自己清空，中途卸载则由 onUnmounted 调用。
  let resizeCleanup = null
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
      resizeCleanup = null
      document.removeEventListener('mousemove', move)
      document.removeEventListener('mouseup', up)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      localStorage.setItem('fm_side_w', String(sideW.value))
      localStorage.setItem('fm_info_w', String(infoW.value))
    }
    document.addEventListener('mousemove', move)
    document.addEventListener('mouseup', up)
    // 同框选：中途卸载就等不到 mouseup，另外 body 的 cursor/userSelect 也会卡在拖拽态，
    // 影响的是整个应用而不只是本页。
    resizeCleanup = up
  }

  onMounted(() => {
    loadCwd()
    loadConf()
  })
  onUnmounted(() => {
    // 单击延迟：250ms 内离开本页的话，selectFile 会在已卸载的组件上跑起来，
    // 白发一次取字节请求。
    clearTimeout(clickTimer)
    // 拖拽途中卸载：这个还挂在 document 上（框选那个由 useSelection 自己摘）。
    resizeCleanup?.()
  })

  return {
    // 直传模块（模板里 fm.previewUrl 等）
    fm,
    // 核心状态
    cwd,
    loading,
    q,
    viewMode,
    showInfo,
    isMobile,
    searchOpen,
    searchInputRef,
    sheet,
    effectiveView,
    drive,
    drivePct,
    treeRef,
    treeKey,
    tableRef,
    fileInput,
    dirInput,
    contentEl,
    sideW,
    infoW,
    viewOptions,
    treeProps,
    filtered,
    crumbs,
    destDlg,
    // 核心方法
    openSearch,
    closeSearch,
    goto,
    loadTreeNode,
    onTreeClick,
    onRowClick,
    onRowDblclick,
    onCellTap,
    openSheet,
    sheetDo,
    newFolder,
    doRename,
    doDeleteMany,
    onRowCommand,
    startMoveCopy,
    confirmDest,
    startResize,
    isImage,
    isText,
    iconOf,
    tintOf,
    // 分组：五个子模块（reactive 包一层，模板里 sel.selPaths 才会自动解包 ref）
    sel: reactive(sel),
    preview: reactive(preview),
    transfers: reactive(transfers),
    dnd: reactive(dnd),
    ctxmenu: reactive(ctxmenu),
    // 格式化（模板沿用旧名 fmtSize / fmtDate）
    fmtSize: fmtBytes,
    fmtDate: fmtMonthDay,
  }
}
