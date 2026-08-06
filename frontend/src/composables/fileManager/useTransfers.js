// 文件管理器 · 传输列表（上传 / 下载，useFileManager 的内部实现，不单独对外用）。
//
// 输入：
//   fm         fmApi 模块（直传七牛 / 带进度下载 / 302 兜底 URL）
//   conf       reactive 全局设置（上传、下载并发数）
//   cwd        ref<string>  上传目标目录；上传完成后若已切走就不刷新当前列表
//   loadCwd / reloadTree   传完刷新列表 / 目录树
//   fileInput / dirInput   两个隐藏 <input type=file>，由视图持有
//
// 输出：tasks / tasksCollapsed / activeCount / addTask / doDownload / doDownloadMany /
//   uploadMany / pickFiles / pickFolder / onFilesPicked。
import { ref, reactive, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { saveBlob } from '../../utils/saveBlob'

export function useTransfers({ fm, conf, cwd, loadCwd, reloadTree, fileInput, dirInput }) {
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

  // 下载：直连七牛流式取字节（传输列表里显示进度），失败回退 302 浏览器直接下载
  async function doDownload(row) {
    const t = addTask('down', row.name)
    try {
      const blob = await fm.downloadBlob(row.path, (p) => (t.pct = Math.round(p * 100)), row.size)
      saveBlob(blob, row.name)
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

  // ---------- 上传（直传七牛，支持文件夹；并发数走全局配置） ----------
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

  return {
    tasks,
    tasksCollapsed,
    activeCount,
    addTask,
    doDownload,
    doDownloadMany,
    uploadMany,
    pickFiles,
    pickFolder,
    onFilesPicked,
  }
}
