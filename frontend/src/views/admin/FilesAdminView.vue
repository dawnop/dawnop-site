<script setup>
import { ref, onMounted } from 'vue'
import { filesApi } from '../../api'
import FilePreview from '../../components/FilePreview.vue'

const items = ref([])
const total = ref(0)
const page = ref(1)
const size = 20
const loading = ref(true)
const uploading = ref(false)
const fileInput = ref(null)

// 当前预览：{ file, url }
const preview = ref(null)

async function load() {
  loading.value = true
  try {
    const { data } = await filesApi.list(page.value, size)
    items.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function pick() {
  fileInput.value.click()
}

async function onUpload(e) {
  const file = e.target.files[0]
  if (!file) return
  uploading.value = true
  try {
    await filesApi.upload(file)
    e.target.value = ''
    page.value = 1
    load()
  } finally {
    uploading.value = false
  }
}

async function openPreview(f) {
  preview.value = { file: f, url: '' }
  const { data } = await filesApi.url(f.id)
  preview.value = { file: f, url: data.url }
}

async function copyUrl(f) {
  const { data } = await filesApi.url(f.id)
  await navigator.clipboard.writeText(data.url)
  alert('已复制带签名的临时链接')
}

async function remove(f) {
  if (!confirm(`确定删除 ${f.filename}？`)) return
  await filesApi.remove(f.id)
  if (preview.value?.file.id === f.id) preview.value = null
  load()
}

function fmtSize(n) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

onMounted(load)
</script>

<template>
  <div>
    <div class="toolbar">
      <h1>文件管理</h1>
      <div class="actions">
        <input ref="fileInput" type="file" hidden @change="onUpload" />
        <button class="primary" :disabled="uploading" @click="pick">
          {{ uploading ? '上传中…' : '上传文件' }}
        </button>
      </div>
    </div>

    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="items.length === 0" class="muted">还没有文件。</p>

    <table v-else class="grid">
      <thead>
        <tr>
          <th>文件名</th>
          <th>类型</th>
          <th>大小</th>
          <th class="ops">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="f in items" :key="f.id">
          <td>{{ f.filename }}</td>
          <td class="muted">{{ f.content_type || '—' }}</td>
          <td class="muted">{{ fmtSize(f.size) }}</td>
          <td class="ops">
            <a href="#" @click.prevent="openPreview(f)">预览</a>
            <a href="#" @click.prevent="copyUrl(f)">复制链接</a>
            <a href="#" class="danger" @click.prevent="remove(f)">删除</a>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="total > size" class="pager">
      <button :disabled="page <= 1" @click="(page--, load())">上一页</button>
      <span class="muted">第 {{ page }} 页</span>
      <button :disabled="page * size >= total" @click="(page++, load())">下一页</button>
    </div>

    <div v-if="preview" class="preview-panel">
      <div class="preview-head">
        <strong>{{ preview.file.filename }}</strong>
        <button @click="preview = null">关闭</button>
      </div>
      <FilePreview :file="preview.file" :url="preview.url" />
    </div>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18px;
}
.grid {
  width: 100%;
  border-collapse: collapse;
}
.grid th,
.grid td {
  text-align: left;
  padding: 10px 8px;
  border-bottom: 1px solid var(--border);
}
.grid th {
  font-size: 0.85rem;
  color: var(--muted);
  font-weight: 600;
}
.ops {
  white-space: nowrap;
}
.ops a {
  margin-right: 12px;
  text-decoration: none;
  font-size: 0.9rem;
}
.ops a.danger {
  color: #b91c1c;
}
.muted {
  color: #8b949e;
}
.pager {
  display: flex;
  gap: 14px;
  align-items: center;
  justify-content: center;
  margin-top: 24px;
}
.preview-panel {
  margin-top: 28px;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
}
.preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
</style>
