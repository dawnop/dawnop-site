<script setup>
import { ref, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { articlesApi, pagesApi } from '../../api'

const items = ref([])
const total = ref(0)
const page = ref(1)
const size = 20
const loading = ref(true)
const error = ref('')
const importing = ref(false)
const fileInput = ref(null)
const pageMap = ref({}) // page_id -> title

function pageName(id) {
  return id ? pageMap.value[id] || `#${id}` : '—'
}

function articleUrl(slug) {
  return `${location.origin}/#/article/${slug}`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [arts, pages] = await Promise.all([
      articlesApi.listAll(page.value, size),
      pagesApi.listAll(),
    ])
    items.value = arts.data.items
    total.value = arts.data.total
    pageMap.value = Object.fromEntries(pages.data.map((p) => [p.id, p.title]))
  } catch (e) {
    error.value = '加载失败，请确认后端已启动并已登录。'
  } finally {
    loading.value = false
  }
}

async function remove(a) {
  if (!confirm(`确定删除《${a.title}》？`)) return
  await articlesApi.remove(a.id)
  load()
}

async function togglePublish(a) {
  await articlesApi.update(a.id, { published: !a.published })
  load()
}

function pickImport() {
  fileInput.value.click()
}

async function onImport(e) {
  const file = e.target.files[0]
  if (!file) return
  importing.value = true
  try {
    await articlesApi.importMarkdown(file, false)
    e.target.value = ''
    page.value = 1
    load()
  } finally {
    importing.value = false
  }
}

async function exportMd(a) {
  const { data } = await articlesApi.exportMarkdown(a.id)
  const url = URL.createObjectURL(data)
  const link = document.createElement('a')
  link.href = url
  link.download = `${a.slug}.md`
  link.click()
  URL.revokeObjectURL(url)
}

function fmtDate(s) {
  return new Date(s).toLocaleDateString('zh-CN')
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <h1>文章管理</h1>
      <div class="actions">
        <input ref="fileInput" type="file" accept=".md,text/markdown" hidden @change="onImport" />
        <button :disabled="importing" @click="pickImport">
          {{ importing ? '导入中…' : '导入 .md' }}
        </button>
        <RouterLink to="/admin/articles/new"><button class="primary">写文章</button></RouterLink>
      </div>
    </div>

    <div class="card">
      <p v-if="error" class="error">{{ error }}</p>
      <p v-else-if="loading" class="muted">加载中…</p>
      <p v-else-if="items.length === 0" class="empty">还没有文章。</p>

      <table v-else class="data-grid">
        <thead>
          <tr>
            <th>标题</th>
            <th>链接</th>
            <th>所属页面</th>
            <th>状态</th>
            <th>更新时间</th>
            <th class="ops">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="a in items" :key="a.id">
            <td>{{ a.title }}</td>
            <td class="link-cell">
              <a v-if="a.published" :href="articleUrl(a.slug)" target="_blank" rel="noopener" :title="articleUrl(a.slug)">/article/{{ a.slug }}</a>
              <span v-else class="muted" :title="'草稿未发布，前台不可见'">/article/{{ a.slug }}</span>
            </td>
            <td class="muted">{{ pageName(a.page_id) }}</td>
            <td>
              <span :class="['badge', a.published ? 'pub' : 'draft']">
                {{ a.published ? '已发布' : '草稿' }}
              </span>
            </td>
            <td class="muted">{{ fmtDate(a.updated_at) }}</td>
            <td class="ops">
              <RouterLink :to="`/admin/articles/${a.id}/edit`">编辑</RouterLink>
              <a @click.prevent="togglePublish(a)">{{ a.published ? '转草稿' : '发布' }}</a>
              <a @click.prevent="exportMd(a)">导出</a>
              <a class="danger" @click.prevent="remove(a)">删除</a>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="total > size" class="pager">
        <button :disabled="page <= 1" @click="(page--, load())">上一页</button>
        <span class="muted">第 {{ page }} 页</span>
        <button :disabled="page * size >= total" @click="(page++, load())">下一页</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.link-cell {
  font-size: 0.85rem;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.link-cell a {
  text-decoration: none;
}
</style>
