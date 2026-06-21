<script setup>
import { ref, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { articlesApi } from '../api'

const items = ref([])
const total = ref(0)
const page = ref(1)
const size = 10
const loading = ref(true)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await articlesApi.listPublished(page.value, size)
    items.value = data.items
    total.value = data.total
  } catch (e) {
    error.value = '加载失败'
  } finally {
    loading.value = false
  }
}

function fmtDate(s) {
  return new Date(s).toLocaleDateString('zh-CN')
}

function go(p) {
  page.value = p
  load()
}

onMounted(load)
</script>

<template>
  <div>
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="muted">{{ error }}</p>
    <p v-else-if="items.length === 0" class="muted">还没有文章。</p>

    <ul v-else class="post-list">
      <li v-for="a in items" :key="a.id" class="post">
        <RouterLink :to="`/article/${a.slug}`" class="post-title">{{ a.title }}</RouterLink>
        <div class="post-meta">{{ fmtDate(a.created_at) }}</div>
        <p v-if="a.summary" class="post-summary">{{ a.summary }}</p>
      </li>
    </ul>

    <div v-if="total > size" class="pager">
      <button :disabled="page <= 1" @click="go(page - 1)">上一页</button>
      <span class="muted">第 {{ page }} 页</span>
      <button :disabled="page * size >= total" @click="go(page + 1)">下一页</button>
    </div>
  </div>
</template>

<style scoped>
.post-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.post {
  padding: 20px 0;
  border-bottom: 1px solid var(--border);
}
.post-title {
  font-size: 1.3rem;
  font-weight: 600;
  text-decoration: none;
  color: var(--fg);
}
.post-title:hover {
  color: var(--accent);
}
.post-meta {
  color: #8b949e;
  font-size: 0.85rem;
  margin-top: 4px;
}
.post-summary {
  color: var(--muted);
  margin: 10px 0 0;
}
.muted {
  color: #8b949e;
}
.pager {
  display: flex;
  gap: 14px;
  align-items: center;
  justify-content: center;
  margin-top: 28px;
}
</style>
