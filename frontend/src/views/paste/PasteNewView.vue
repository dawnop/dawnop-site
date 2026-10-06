<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import MarkdownDocument from '../../components/MarkdownDocument.vue'
import { createPaste, getConfig, errorText } from '../../api/pasteApi'
import { markdownBytes, validateMarkdown, importMarkdown } from '../../utils/safeMarkdown'
import { useUnsavedGuard } from '../../composables/useUnsavedGuard'
const router = useRouter()
const seed = window.history.state?.pasteDraft
const title = ref(seed?.title || ''),
  content = ref(seed?.content || ''),
  days = ref(7),
  mode = ref('正文')
const maxBytes = ref(262144),
  ready = ref(false),
  busy = ref(false),
  importing = ref(false),
  error = ref(''),
  fileInput = ref(null)
const size = computed(() => markdownBytes(content.value))
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
    mode.value = '正文'
  } catch (e) {
    if (e instanceof Error) ElMessage.error(e.message)
  } finally {
    importing.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}
function drop(event) {
  if (event.dataTransfer.files.length !== 1) {
    ElMessage.error('每次导入一个 .md 文件')
    return
  }
  importFile(event.dataTransfer.files[0])
}
</script>
<template>
  <form @submit.prevent="publish" @dragover.prevent @drop.prevent="drop">
    <el-input
      v-model="title"
      placeholder="标题（可选）"
      aria-label="标题"
      :disabled="busy"
      class="paste-title-input"
    />
    <div class="paste-toolbar">
      <el-radio-group v-model="mode" size="small"
        ><el-radio-button label="正文" /><el-radio-button label="预览" /></el-radio-group
      ><el-button text :disabled="!ready || busy || importing" @click="fileInput.click()"
        >导入 .md</el-button
      ><input
        ref="fileInput"
        type="file"
        accept=".md"
        hidden
        @change="importFile($event.target.files[0])"
      /><span class="paste-meta">{{ (size / 1024).toFixed(1) }} / {{ maxBytes / 1024 }} KiB</span>
    </div>
    <el-input
      v-if="mode === '正文'"
      v-model="content"
      type="textarea"
      :rows="20"
      resize="vertical"
      placeholder="Markdown"
      aria-label="Markdown 正文"
      :disabled="busy"
    />
    <MarkdownDocument v-else class="paste-preview" :source="content" />
    <div class="paste-publish">
      <span class="paste-meta">发布后公开可搜索</span
      ><el-select v-model="days" aria-label="保存期限" :disabled="busy"
        ><el-option v-for="n in [1, 7, 30]" :key="n" :label="`${n} 天`" :value="n" /></el-select
      ><el-button
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="!ready || importing || size > maxBytes"
        >发布</el-button
      >
    </div>
    <p v-if="error" role="alert" class="paste-error">
      {{ error }} <el-button v-if="!ready" text @click="config">重试</el-button>
    </p>
  </form>
</template>
<style scoped>
.paste-title-input {
  margin-bottom: 20px;
}
.paste-toolbar .paste-meta {
  margin-left: auto;
}
.paste-preview {
  min-height: 420px;
  border: 1px solid var(--border);
  padding: 16px;
  border-radius: 8px;
}
.paste-publish {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-top: 20px;
}
.paste-publish .paste-meta {
  flex: 1;
}
.paste-publish .el-select {
  width: 96px;
}
</style>
