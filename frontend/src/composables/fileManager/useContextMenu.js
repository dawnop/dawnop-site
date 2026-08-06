// 文件管理器 · 右键菜单（useFileManager 的内部实现，不单独对外用）。
//
// 输入：
//   selPaths / selRows / selectedPath  归 useSelection 所有：右键落在已多选的行上 →
//                                      对整个选中集操作，否则改成单选那一行
//   actions   菜单项真正要跑的动作，全部由 core / 其余子模块提供：
//             newFolder / pickFiles / pickFolder / loadCwd / goto / selectFile /
//             doDownload / doDownloadMany / doRename / startMoveCopy / doDelete / doDeleteMany
//
// 输出：menu（reactive：show / x / y / rows）、items（computed 菜单项）、
//   openRow / openBlank / onTableRowCtx / run。
// 本模块自己挂 onMounted / onUnmounted：点别处关菜单、Esc 关菜单。
import { reactive, computed, onMounted, onUnmounted } from 'vue'
import {
  FolderAdd,
  FolderOpened,
  Files,
  RefreshRight,
  View,
  Download,
  EditPen,
  Right,
  CopyDocument,
  Delete,
} from '@element-plus/icons-vue'

export function useContextMenu({ selPaths, selRows, selectedPath, actions }) {
  const menu = reactive({ show: false, x: 0, y: 0, rows: [] })

  function open(ev, rows) {
    ev.preventDefault()
    ev.stopPropagation()
    menu.rows = rows
    // 贴边时向内收，避免菜单出屏
    menu.x = Math.min(ev.clientX, window.innerWidth - 200)
    menu.y = Math.min(ev.clientY, window.innerHeight - (items.value.length * 34 + 24))
    menu.show = true
  }
  function openRow(ev, row) {
    // 右键落在已多选的行上 → 对整个选中集操作；否则单行
    if (selPaths.value.length > 1 && selPaths.value.includes(row.path)) {
      open(ev, selRows.value)
    } else {
      selectedPath.value = row.path
      open(ev, [row])
    }
  }
  function openBlank(ev) {
    open(ev, [])
  }
  function onTableRowCtx(row, _col, ev) {
    openRow(ev, row)
  }
  function close() {
    menu.show = false
  }
  function run(item) {
    close()
    item.run()
  }
  function onGlobalKey(e) {
    if (e.key === 'Escape') close()
  }

  const items = computed(() => {
    const rows = menu.rows
    if (!rows.length) {
      return [
        { label: '新建文件夹', icon: FolderAdd, run: actions.newFolder },
        { label: '上传文件', icon: Files, run: actions.pickFiles },
        { label: '上传文件夹', icon: FolderOpened, run: actions.pickFolder },
        { label: '刷新', icon: RefreshRight, divided: true, run: actions.loadCwd },
      ]
    }
    if (rows.length === 1) {
      const r = rows[0]
      const one = []
      if (r.is_dir) one.push({ label: '打开', icon: FolderOpened, run: () => actions.goto(r.path) })
      else {
        one.push({ label: '预览', icon: View, run: () => actions.selectFile(r) })
        one.push({ label: '下载', icon: Download, run: () => actions.doDownload(r) })
      }
      one.push({ label: '重命名', icon: EditPen, run: () => actions.doRename(r) })
      one.push({ label: '移动到…', icon: Right, run: () => actions.startMoveCopy('move', [r]) })
      one.push({
        label: '复制到…',
        icon: CopyDocument,
        run: () => actions.startMoveCopy('copy', [r]),
      })
      one.push({
        label: '删除',
        icon: Delete,
        danger: true,
        divided: true,
        run: () => actions.doDelete(r),
      })
      return one
    }
    const files = rows.filter((r) => !r.is_dir)
    return [
      ...(files.length
        ? [
            {
              label: `下载 ${files.length} 个文件`,
              icon: Download,
              run: () => actions.doDownloadMany(rows),
            },
          ]
        : []),
      {
        label: `移动 ${rows.length} 项到…`,
        icon: Right,
        run: () => actions.startMoveCopy('move', rows),
      },
      {
        label: `复制 ${rows.length} 项到…`,
        icon: CopyDocument,
        run: () => actions.startMoveCopy('copy', rows),
      },
      {
        label: `删除 ${rows.length} 项`,
        icon: Delete,
        danger: true,
        divided: true,
        run: () => actions.doDeleteMany(rows),
      },
    ]
  })

  onMounted(() => {
    document.addEventListener('click', close)
    document.addEventListener('keydown', onGlobalKey)
  })
  onUnmounted(() => {
    document.removeEventListener('click', close)
    document.removeEventListener('keydown', onGlobalKey)
  })

  return { menu, items, openRow, openBlank, onTableRowCtx, run }
}
