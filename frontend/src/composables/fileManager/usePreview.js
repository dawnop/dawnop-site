// 文件管理器 · 预览与在线编辑（useFileManager 的内部实现，不单独对外用）。
//
// 输入：
//   fm            fmApi 模块（取字节 / 存文本 / 签名 URL）
//   selectedPath  ref<string>  归 useSelection 所有；预览要认领「响应回来时还选着同一行」
//   showInfo      ref<boolean> 点文件时弹出右侧预览面板
//   conf          reactive 全局设置（文本预览大小上限）
//   isImage / isText / textTooLarge  展示辅助（core 提供，避免两处判定漂移）
//   loadCwd       保存文本后刷新列表（大小变了）
//
// 输出：previewText / previewErr / imgViewer / modal / selectFile / openImgViewer /
//   openModal / startEdit / saveEdit / beforeCloseModal。
import { ref, reactive, computed, toRaw, onBeforeUnmount } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

export function usePreview({
  fm,
  selectedPath,
  showInfo,
  conf,
  isImage,
  isText,
  textTooLarge,
  loadCwd,
}) {
  const previewText = ref('')
  const previewErr = ref('')

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
    view: '正文',
    saved: false,
  })
  async function openModal(row) {
    if (modal.saving || !(await confirmDiscard())) return false
    selectedPath.value = row.path
    if (isImage(row)) return openImgViewer(row)
    modal.row = row
    modal.view = '正文'
    modal.saved = false
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
        if (toRaw(modal.row) !== toRaw(row)) return
        modal.text = text
        modal.loaded = true
        return true
      } catch (e) {
        if (toRaw(modal.row) !== toRaw(row)) return
        modal.err = e.message || '预览失败'
      }
    }
  }
  function startEdit() {
    if (!modal.loaded || modal.saving) return
    modal.saved = false
    modal.draft = modal.text
    modal.editing = true
  }
  const dirty = computed(() => modal.show && modal.editing && modal.draft !== modal.text)
  const saveState = computed(() =>
    modal.saving ? '保存中…' : dirty.value ? '未保存' : modal.saved ? '已保存' : '',
  )
  async function saveEdit() {
    if (modal.saving || !modal.loaded || !modal.editing) return
    const row = modal.row,
      content = modal.draft
    modal.saving = true
    try {
      await fm.saveText(row.path, content)
      if (toRaw(modal.row) !== toRaw(row)) return
      modal.text = content
      modal.saved = true
      ElMessage.success('已保存')
      if (selectedPath.value === row.path) previewText.value = content
      loadCwd()
    } catch {
      // 请求失败保留草稿，由接口层提示。
    } finally {
      modal.saving = false
    }
  }
  async function confirmDiscard() {
    if (modal.saving) return false
    if (!dirty.value) return true
    try {
      await ElMessageBox.confirm('有未保存的修改，确定放弃？', '未保存', {
        confirmButtonText: '放弃',
        cancelButtonText: '继续编辑',
        type: 'warning',
      })
      return true
    } catch {
      return false
    }
  }
  async function cancelEdit() {
    if (await confirmDiscard()) {
      modal.draft = modal.text
      modal.editing = false
    }
  }
  async function beforeCloseModal(done) {
    if (await confirmDiscard()) {
      modal.row = null
      modal.editing = false
      done()
    }
  }
  function editKey(event) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      event.preventDefault()
      saveEdit()
    }
  }
  function beforeUnload(event) {
    if (dirty.value || modal.saving) {
      event.preventDefault()
      event.returnValue = ''
    }
  }
  window.addEventListener('beforeunload', beforeUnload)
  onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
  onBeforeRouteLeave(confirmDiscard)

  return {
    previewText,
    previewErr,
    imgViewer,
    modal,
    selectFile,
    openImgViewer,
    openModal,
    startEdit,
    saveEdit,
    beforeCloseModal,
    cancelEdit,
    editKey,
    saveState,
  }
}
