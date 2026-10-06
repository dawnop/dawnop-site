<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import MarkdownDocument from '../../components/MarkdownDocument.vue'
import { getPaste, errorText } from '../../api/pasteApi'
import { fmtDateTime } from '../../utils/format'
import { setTitle } from '../../utils/title'
const route = useRoute(),
  router = useRouter()
const paste = ref(null),
  loading = ref(false),
  error = ref(''),
  mode = ref('预览')
const origin = window.location.origin
let revision = 0
async function load() {
  const ticket = ++revision
  loading.value = true
  paste.value = null
  error.value = ''
  mode.value = '预览'
  try {
    const data = await getPaste(route.params.id)
    if (ticket !== revision) return
    paste.value = data
    setTitle(data.title || 'Paste')
  } catch (e) {
    if (ticket === revision)
      error.value = e.response?.status === 404 ? '404 · Paste 不存在' : errorText(e)
  } finally {
    if (ticket === revision) loading.value = false
  }
}
watch(() => route.params.id, load, { immediate: true })
async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败，请手动选择正文')
  }
}
function download() {
  const url = URL.createObjectURL(
    new Blob([paste.value.content], { type: 'text/markdown;charset=utf-8' }),
  )
  const link = document.createElement('a')
  link.href = url
  // eslint-disable-next-line no-control-regex -- 清理下载文件名。
  link.download = `${(paste.value.title || paste.value.id).replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').slice(0, 120)}.md`
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
function duplicate() {
  router.push({
    path: '/paste/new',
    state: { pasteDraft: { title: paste.value.title, content: paste.value.content } },
  })
}
</script>
<template>
  <div v-loading="loading">
    <div v-if="error" class="paste-error" role="alert">
      {{ error }} <el-button v-if="!error.startsWith('404')" text @click="load">重试</el-button>
    </div>
    <article v-if="paste">
      <h1 v-if="paste.title">{{ paste.title }}</h1>
      <p class="paste-meta">
        {{ fmtDateTime(paste.created_at * 1000) }} ·
        {{ paste.expires_at ? `${fmtDateTime(paste.expires_at * 1000)} 到期` : '永久' }}
      </p>
      <div class="paste-toolbar">
        <el-radio-group v-model="mode" size="small"
          ><el-radio-button label="预览" /><el-radio-button label="原文" /></el-radio-group
        ><el-button text @click="copy(paste.content)">复制正文</el-button
        ><el-button text @click="copy(`${origin}/paste/${paste.id}`)">复制链接</el-button
        ><el-button text @click="download">下载 .md</el-button
        ><el-button text @click="duplicate">复制为新条目</el-button>
      </div>
      <MarkdownDocument v-if="mode === '预览'" :source="paste.content" />
      <pre v-else class="paste-source">{{ paste.content }}</pre>
    </article>
  </div>
</template>
<style scoped>
h1 {
  font-size: 24px;
  font-weight: 600;
  margin: 0 0 12px;
  overflow-wrap: anywhere;
}
.paste-meta {
  margin: 0 0 24px;
}
.paste-toolbar {
  gap: 8px;
}
.paste-toolbar .el-button + .el-button {
  margin-left: 0;
}
</style>
