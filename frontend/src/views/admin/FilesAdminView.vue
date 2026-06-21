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
const progress = ref({ done: 0, total: 0 })
const dragging = ref(false)
const fileInput = ref(null)
const dirInput = ref(null)

const preview = ref(null) // { file, url }

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

// 统一上传入口：list = [{ file, path }]
async function uploadList(list) {
  if (!list.length) return
  uploading.value = true
  progress.value = { done: 0, total: list.length }
  try {
    for (const { file, path } of list) {
      await filesApi.upload(file, path)
      progress.value.done++
    }
    page.value = 1
    await load()
  } finally {
    uploading.value = false
  }
}

function fromFileList(fileList) {
  return Array.from(fileList).map((f) => ({
    file: f,
    path: f.webkitRelativePath || f.name,
  }))
}

// ---- 选择文件 / 文件夹 ----
function pickFiles() {
  fileInput.value.click()
}
function pickDir() {
  dirInput.value.click()
}
async function onPick(e) {
  await uploadList(fromFileList(e.target.files))
  e.target.value = ''
}

// ---- 拖拽（支持拖入文件夹）----
function readEntries(reader) {
  return new Promise((res, rej) => reader.readEntries(res, rej))
}
async function traverse(entry, prefix, out) {
  if (entry.isFile) {
    const file = await new Promise((res, rej) => entry.file(res, rej))
    out.push({ file, path: prefix + entry.name })
  } else if (entry.isDirectory) {
    const reader = entry.createReader()
    let batch
    do {
      batch = await readEntries(reader)
      for (const child of batch) await traverse(child, prefix + entry.name + '/', out)
    } while (batch.length > 0)
  }
}

async function onDrop(e) {
  dragging.value = false
  const dt = e.dataTransfer
  const items_ = dt.items
  if (items_ && items_.length && items_[0].webkitGetAsEntry) {
    const entries = Array.from(items_)
      .map((i) => i.webkitGetAsEntry())
      .filter(Boolean)
    const out = []
    for (const en of entries) await traverse(en, '', out)
    await uploadList(out)
  } else {
    await uploadList(fromFileList(dt.files))
  }
}

// ---- 预览 / 链接 / 删除 ----
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
        <input ref="fileInput" type="file" multiple hidden @change="onPick" />
        <input ref="dirInput" type="file" webkitdirectory hidden @change="onPick" />
        <button @click="pickFiles" :disabled="uploading">上传文件</button>
        <button @click="pickDir" :disabled="uploading">导入文件夹</button>
      </div>
    </div>

    <!-- 拖拽区 -->
    <div
      class="dropzone"
      :class="{ active: dragging, busy: uploading }"
      @dragover.prevent="dragging = true"
      @dragenter.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onDrop"
    >
      <template v-if="uploading">
        上传中… {{ progress.done }} / {{ progress.total }}
      </template>
      <template v-else> 把文件或文件夹拖到这里上传（保留文件夹路径） </template>
    </div>

    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="items.length === 0" class="muted">还没有文件。</p>

    <table v-else class="grid">
      <thead>
        <tr>
          <th>文件名 / 路径</th>
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
  margin-bottom: 16px;
}
.actions {
  display: flex;
  gap: 10px;
}
.dropzone {
  border: 2px dashed #d0d7de;
  border-radius: 10px;
  padding: 26px;
  text-align: center;
  color: #8b949e;
  margin-bottom: 22px;
  transition: border-color 0.15s, background 0.15s;
}
.dropzone.active {
  border-color: var(--accent);
  background: #eff6ff;
  color: var(--accent);
}
.dropzone.busy {
  color: var(--fg);
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
