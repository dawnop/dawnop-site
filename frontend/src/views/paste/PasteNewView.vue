<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { Upload, Document, ArrowRight } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import MarkdownDocument from '../../components/MarkdownDocument.vue'
import { createPaste, getConfig, errorText } from '../../api/pasteApi'
import { markdownBytes, validateMarkdown, importMarkdown } from '../../utils/safeMarkdown'
import { useUnsavedGuard } from '../../composables/useUnsavedGuard'
const router = useRouter()
const media = window.matchMedia('(min-width: 960px)')
const wide = ref(media.matches)
const dragging = ref(false)
let dragDepth = 0
function resize() {
  wide.value = media.matches
  if (!wide.value && mode.value === '分栏') mode.value = '正文'
}
onMounted(() => media.addEventListener('change', resize))
onBeforeUnmount(() => media.removeEventListener('change', resize))
const seed = window.history.state?.pasteDraft
const title = ref(seed?.title || ''),
  content = ref(seed?.content || ''),
  days = ref(7),
  mode = ref(wide.value ? '分栏' : '正文')
const maxBytes = ref(262144),
  ready = ref(false),
  busy = ref(false),
  importing = ref(false),
  error = ref(''),
  fileInput = ref(null)
const size = computed(() => markdownBytes(content.value))
const lines = computed(() => content.value.split('\n').length)
const modes = computed(() => (wide.value ? ['正文', '分栏', '预览'] : ['正文', '预览']))
const guard = useUnsavedGuard(() => JSON.stringify([title.value, content.value, days.value]))
async function config() {
  error.value = ''
  try {
    maxBytes.value = (await getConfig()).max_bytes
    ready.value = true
  } catch (e) {
    error.value = errorText(e)
  }
}
onMounted(config)
async function publish() {
  if (!ready.value || busy.value || importing.value) return
  error.value = ''
  try {
    validateMarkdown(content.value, maxBytes.value)
    if (Array.from(title.value).length > 120) throw new Error('标题最多 120 字')
    busy.value = true
    const paste = await createPaste({
      title: title.value,
      content: content.value,
      days: days.value,
    })
    guard.markSaved()
    await router.push(`/paste/${paste.id}`)
  } catch (e) {
    error.value = e.response ? errorText(e) : e.message
  } finally {
    busy.value = false
  }
}
async function importFile(file) {
  if (!file || busy.value || importing.value || !ready.value) return
  importing.value = true
  try {
    const source = await importMarkdown(file, maxBytes.value)
    // 覆盖草稿是明确动作，失败的导入不改变已有输入。
    if (content.value && content.value !== source) {
      await ElMessageBox.confirm('替换当前正文？', '导入 Markdown', {
        confirmButtonText: '替换',
        cancelButtonText: '取消',
      })
    }
    content.value = source
    if (!title.value) title.value = file.name.replace(/\.md$/i, '')
    error.value = ''
    mode.value = wide.value ? '分栏' : '正文'
  } catch (e) {
    if (e instanceof Error) ElMessage.error(e.message)
  } finally {
    importing.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}
function dragEnter(event) {
  if (!event.dataTransfer.types.includes('Files')) return
  event.preventDefault()
  dragDepth++
  dragging.value = true
}
function dragLeave() {
  dragDepth = Math.max(0, dragDepth - 1)
  if (!dragDepth) dragging.value = false
}
function dragOver(event) {
  if (event.dataTransfer.types.includes('Files')) event.preventDefault()
}
function drop(event) {
  dragDepth = 0
  dragging.value = false
  if (!event.dataTransfer.files.length) return
  event.preventDefault()
  if (event.dataTransfer.files.length !== 1) {
    ElMessage.error('每次导入一个 .md 文件')
    return
  }
  importFile(event.dataTransfer.files[0])
}
</script>
<template>
  <form
    class="paste-compose"
    @submit.prevent="publish"
    @dragenter="dragEnter"
    @dragleave="dragLeave"
    @dragover="dragOver"
    @drop="drop"
  >
    <div class="compose-heading">
      <h1 class="paste-heading">新建</h1>
      <el-button
        text
        :icon="Upload"
        :disabled="!ready || busy || importing"
        :loading="importing"
        @click="fileInput.click()"
        >导入 .md</el-button
      >
      <input
        ref="fileInput"
        type="file"
        accept=".md"
        hidden
        @change="importFile($event.target.files[0])"
      />
    </div>
    <div class="compose-document">
      <div class="compose-title">
        <input
          v-model="title"
          placeholder="标题（可选）"
          aria-label="标题"
          :disabled="busy || importing"
        />
      </div>
      <div class="compose-tools">
        <div class="paste-tabs" role="group" aria-label="编辑视图">
          <button
            v-for="tab in modes"
            :key="tab"
            type="button"
            :aria-pressed="mode === tab"
            :disabled="busy"
            @click="mode = tab"
          >
            {{ tab }}
          </button>
        </div>
        <span class="paste-meta compose-format">Markdown</span>
      </div>
      <div class="compose-workspace" :class="{ 'is-split': mode === '分栏' }">
        <textarea
          v-show="mode !== '预览'"
          v-model="content"
          class="compose-input"
          aria-label="Markdown 正文"
          placeholder="写点什么…"
          :disabled="busy || importing"
          spellcheck="false"
          @keydown.ctrl.enter.prevent="publish"
          @keydown.meta.enter.prevent="publish"
        />
        <div v-if="mode !== '正文'" class="compose-preview" aria-label="Markdown 预览">
          <MarkdownDocument v-if="content.trim()" :source="content" />
          <div v-else class="compose-preview-empty">
            <el-icon><Document /></el-icon><span>预览</span>
          </div>
        </div>
      </div>
      <div class="compose-status paste-meta">
        <span>{{ lines }} 行</span>
        <span :class="{ 'over-limit': size > maxBytes }"
          >{{ (size / 1024).toFixed(1) }} / {{ maxBytes / 1024 }} KiB</span
        >
      </div>
      <div v-if="dragging" class="compose-drop" aria-live="polite">
        <el-icon><Upload /></el-icon><span>松开导入 .md</span>
      </div>
    </div>
    <div class="paste-publish">
      <span class="paste-meta publish-visibility">公开 · 可搜索</span>
      <div class="publish-actions">
        <label for="paste-days" class="paste-meta">保留</label>
        <el-select id="paste-days" v-model="days" aria-label="保存期限" :disabled="busy"
          ><el-option v-for="n in [1, 7, 30]" :key="n" :label="`${n} 天`" :value="n"
        /></el-select>
        <el-button
          type="primary"
          native-type="submit"
          :loading="busy"
          :disabled="!ready || importing || size > maxBytes"
          >发布 <el-icon v-if="!busy"><ArrowRight /></el-icon
        ></el-button>
      </div>
    </div>
    <p v-if="error" role="alert" class="compose-error">
      {{ error }} <el-button v-if="!ready" text @click="config">重试</el-button>
    </p>
  </form>
</template>
<style scoped>
.compose-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 28px;
}
.compose-heading .el-button {
  margin-right: -12px;
  color: var(--muted);
}
.compose-document {
  position: relative;
  border: 1px solid var(--border);
  border-radius: var(--el-border-radius-base);
  overflow: hidden;
}
.compose-title {
  padding: 24px 28px;
}
.compose-title input {
  display: block;
  width: 100%;
  border: 0;
  padding: 0;
  outline: 0;
  background: transparent;
  color: var(--fg);
  font: inherit;
  font-size: 22px;
  font-weight: 500;
  line-height: 1.5;
}
.compose-title input::placeholder {
  color: var(--el-text-color-placeholder);
  font-weight: 400;
}
.compose-title:focus-within {
  box-shadow: inset 3px 0 var(--accent);
}
.compose-tools {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 24px;
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
}
.compose-workspace {
  display: grid;
  min-height: 420px;
  height: clamp(420px, 52dvh, 600px);
}
.compose-workspace.is-split {
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
}
.compose-input {
  width: 100%;
  height: 100%;
  min-height: 0;
  border: 0;
  border-radius: 0;
  resize: none;
  outline: none;
  padding: 26px 28px;
  color: var(--fg);
  background: var(--bg);
  font:
    14px/1.9 ui-monospace,
    SFMono-Regular,
    Consolas,
    monospace;
  tab-size: 2;
}
.compose-input::placeholder {
  color: var(--el-text-color-placeholder);
}
.compose-input:focus {
  box-shadow: inset 3px 0 var(--accent);
}
.compose-preview {
  min-width: 0;
  min-height: 0;
  padding: 26px 28px;
  overflow: auto;
}
.is-split .compose-preview {
  border-left: 1px solid var(--border);
}
.compose-preview-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  min-height: 350px;
  gap: 12px;
  color: var(--el-text-color-placeholder);
  font-size: 13px;
}
.compose-preview-empty .el-icon {
  font-size: 28px;
}
.compose-status {
  display: flex;
  justify-content: space-between;
  padding: 10px 24px;
  border-top: 1px solid var(--border);
}
.over-limit {
  color: var(--el-color-danger);
}
.compose-drop {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: 16px;
  background: var(--el-color-primary-light-9);
  color: var(--accent);
  border: 2px dashed var(--accent);
  border-radius: var(--el-border-radius-base);
  pointer-events: none;
}
.compose-drop .el-icon {
  font-size: 36px;
}
.paste-publish {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: center;
  margin-top: 24px;
}
.publish-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.publish-actions .el-select {
  width: 100px;
}
.publish-actions :deep(.el-select__wrapper) {
  min-height: 38px;
}
.publish-actions .el-button {
  height: 38px;
  min-width: 88px;
  gap: 8px;
}
.publish-actions .el-icon {
  margin-left: 8px;
}
.compose-error {
  padding: 12px 16px;
  margin: 20px 0 0;
  border-radius: var(--el-border-radius-base);
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
  font-size: 13px;
}
@media (max-width: 600px) {
  .compose-heading {
    margin-bottom: 24px;
  }
  .compose-title {
    padding: 20px;
  }
  .compose-title input {
    font-size: 20px;
  }
  .compose-tools {
    padding: 10px 16px;
  }
  .compose-input,
  .compose-preview {
    padding: 22px 20px;
  }
  .compose-status {
    padding: 10px 16px;
  }
  .compose-workspace {
    min-height: 320px;
    height: clamp(320px, 45dvh, 460px);
  }
  .compose-preview-empty {
    min-height: 300px;
  }
  .paste-publish {
    flex-wrap: wrap;
    gap: 16px;
    margin-top: 20px;
  }
  .publish-visibility {
    width: 100%;
  }
  .publish-actions {
    width: 100%;
  }
  .publish-actions .el-button {
    margin-left: auto;
  }
}
@media (max-width: 380px) {
  .compose-format {
    display: none;
  }
  .compose-title {
    padding: 18px 16px;
  }
  .compose-input,
  .compose-preview {
    padding: 20px 16px;
  }
}
</style>
