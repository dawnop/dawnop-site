<script setup>
import { ref, watch } from 'vue'
import {
  CopyDocument,
  Link,
  MoreFilled,
  Download,
  DocumentCopy,
  Warning,
} from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import MarkdownDocument from '../../components/MarkdownDocument.vue'
import { getPaste, errorText } from '../../api/pasteApi'
import { fmtDateTime, fmtBytes } from '../../utils/format'
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
function action(command) {
  if (command === 'link') copy(`${origin}/paste/${paste.value.id}`)
  if (command === 'download') download()
  if (command === 'duplicate') duplicate()
}
function duplicate() {
  router.push({
    path: '/paste/new',
    state: { pasteDraft: { title: paste.value.title, content: paste.value.content } },
  })
}
</script>
<template>
  <section class="paste-detail">
    <div v-if="loading" class="detail-loading" aria-busy="true" aria-label="加载中">
      <el-skeleton :rows="8" animated />
    </div>
    <div v-else-if="error" class="paste-state tool-state tool-panel" role="alert">
      <el-icon><Warning /></el-icon>
      <h2>{{ error }}</h2>
      <RouterLink v-if="error.startsWith('404')" to="/paste">查看列表</RouterLink>
      <el-button v-else @click="load">重试</el-button>
    </div>
    <article v-else-if="paste" aria-label="Paste 正文">
      <header class="detail-heading">
        <h1 v-if="paste.title" class="tool-heading">{{ paste.title }}</h1>
        <div class="detail-meta paste-meta">
          <time :datetime="new Date(paste.created_at * 1000).toISOString()">{{
            fmtDateTime(paste.created_at * 1000)
          }}</time>
          <span>{{ fmtBytes(paste.size_bytes) }}</span>
          <span>{{
            paste.expires_at ? `${fmtDateTime(paste.expires_at * 1000)} 到期` : '永久'
          }}</span>
        </div>
      </header>
      <div class="detail-document tool-panel">
        <div class="detail-toolbar">
          <div class="paste-tabs" role="group" aria-label="阅读视图">
            <button
              v-for="tab in ['预览', '原文']"
              :key="tab"
              type="button"
              :aria-pressed="mode === tab"
              @click="mode = tab"
            >
              {{ tab }}
            </button>
          </div>
          <div class="detail-actions">
            <el-button
              text
              :icon="Link"
              class="detail-copy-link"
              @click="copy(`${origin}/paste/${paste.id}`)"
              >复制链接</el-button
            >
            <el-button text :icon="CopyDocument" @click="copy(paste.content)">复制正文</el-button>
            <el-dropdown trigger="click" @command="action">
              <el-button text :icon="MoreFilled" aria-label="更多操作" class="detail-more" />
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="link" :icon="Link">复制链接</el-dropdown-item>
                  <el-dropdown-item command="download" :icon="Download">下载 .md</el-dropdown-item>
                  <el-dropdown-item command="duplicate" :icon="DocumentCopy" divided
                    >复制为新条目</el-dropdown-item
                  >
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>
        <div class="detail-body">
          <MarkdownDocument v-if="mode === '预览'" :source="paste.content" />
          <pre v-else class="paste-source">{{ paste.content }}</pre>
        </div>
      </div>
    </article>
  </section>
</template>
<style scoped>
.paste-detail {
  width: 100%;
}
.detail-heading {
  margin-bottom: var(--tool-heading-gap);
}
.detail-heading h1 {
  overflow-wrap: anywhere;
}
.detail-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  margin-top: 14px;
}
.detail-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--border);
}
.detail-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
.detail-actions .el-button + .el-button {
  margin-left: 0;
}
.detail-actions .el-button {
  padding: 8px 10px;
  font-size: 13px;
}
.detail-actions .detail-more {
  padding: 8px;
}
.detail-body {
  min-height: 320px;
  padding: var(--tool-panel-padding);
}
.detail-body :deep(.markdown-document) {
  font-size: 15px;
  line-height: 1.9;
}
.detail-loading {
  padding: 24px 0;
}
@media (max-width: 600px) {
  .detail-heading {
    margin-bottom: 24px;
  }
  .detail-meta {
    gap: 6px 14px;
  }
  .detail-toolbar {
    padding: 10px 12px;
    gap: 8px;
  }
  .detail-copy-link {
    display: none;
  }
  .detail-body {
    padding: 24px 20px 32px;
  }
  .detail-actions {
    gap: 0;
  }
  .detail-actions .el-button {
    padding: 8px 6px;
  }
}
@media (max-width: 380px) {
  .detail-toolbar {
    padding: 10px 8px;
  }
  .detail-body {
    padding: 22px 16px 28px;
  }
  .paste-tabs button {
    min-width: 48px;
    padding-left: 10px;
    padding-right: 10px;
  }
}
</style>
