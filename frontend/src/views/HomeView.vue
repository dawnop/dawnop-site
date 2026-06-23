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

function readMinutes(wc) {
  return Math.max(1, Math.round((wc || 0) / 300))
}

function go(p) {
  page.value = p
  load()
}

onMounted(load)
</script>

<template>
  <div>
    <el-skeleton v-if="loading" :rows="5" animated />
    <el-empty v-else-if="error" :description="error" />
    <el-empty v-else-if="items.length === 0" description="还没有文章" />

    <ul v-else class="post-list">
      <li v-for="a in items" :key="a.id" class="post">
        <RouterLink :to="`/article/${a.slug}`" class="post-title">{{ a.title }}</RouterLink>
        <p v-if="a.summary" class="post-summary">{{ a.summary }}</p>
        <div class="post-meta">
          <span>{{ fmtDate(a.created_at) }}</span>
          <span class="dot">·</span>
          <span>约 {{ readMinutes(a.word_count) }} 分钟</span>
        </div>
      </li>
    </ul>

    <div v-if="total > size" class="pager">
      <el-pagination
        background
        layout="prev, pager, next"
        :total="total"
        :page-size="size"
        :current-page="page"
        @current-change="go"
      />
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
  padding: 28px 0;
  border-bottom: 1px solid var(--border);
}
.post:first-child {
  padding-top: 8px;
}
.post-title {
  display: inline-block;
  font-size: 1.45rem;
  font-weight: 650;
  line-height: 1.35;
  letter-spacing: -0.01em;
  text-decoration: none;
  color: var(--fg);
  transition: color 0.15s;
}
.post-title:hover {
  color: var(--accent);
}
.post-summary {
  color: var(--muted);
  margin: 10px 0 0;
  line-height: 1.7;
}
.post-meta {
  color: #8b949e;
  font-size: 0.85rem;
  margin-top: 12px;
}
.post-meta .dot {
  margin: 0 8px;
  color: #c9cdd4;
}
.pager {
  display: flex;
  justify-content: center;
  margin-top: 28px;
}
</style>
