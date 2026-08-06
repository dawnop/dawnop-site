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
import { ref, reactive } from 'vue'
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
  }
}
