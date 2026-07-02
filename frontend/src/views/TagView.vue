<script setup>
import { ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { articlesApi } from '../api'

const route = useRoute()
const items = ref([])
const total = ref(0)
const page = ref(1)
const size = 10
const loading = ref(true)
const error = ref('')
const tagName = ref('')

async function load() {
  loading.value = true
  error.value = ''
  const slug = route.params.slug
  try {
    const { data } = await articlesApi.listPublished(page.value, size, slug)
    items.value = data.items
    total.value = data.total
    // 从文章标签里解析出该 slug 的展示名，取不到就退回 slug
    const match = data.items.flatMap((a) => a.tags || []).find((t) => t.slug === slug)
    tagName.value = match ? match.name : slug
    document.title = `#${tagName.value} · dawnop`
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

watch(() => route.params.slug, () => { page.value = 1; load() }, { immediate: true })
</script>

<template>
  <div>
    <div class="tag-head">
      <RouterLink to="/" class="back">← 首页</RouterLink>
      <h1 class="tag-title">
        <span class="hash">#</span>{{ tagName }}
        <span v-if="!loading" class="count">{{ total }} 篇</span>
      </h1>
    </div>

    <el-skeleton v-if="loading" :rows="5" animated />
    <el-empty v-else-if="error" :description="error" />
    <el-empty v-else-if="items.length === 0" description="该标签下暂无文章" />

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
.tag-head {
  margin-bottom: 12px;
}
.back {
  font-size: 0.88rem;
  color: var(--muted);
  text-decoration: none;
}
.back:hover {
  color: var(--accent);
}
.tag-title {
  font-size: 1.7rem;
  font-weight: 700;
  margin: 8px 0 0;
  letter-spacing: -0.01em;
}
.tag-title .hash {
  color: var(--accent);
}
.tag-title .count {
  font-size: 0.9rem;
  font-weight: 400;
  color: var(--muted);
  margin-left: 10px;
}
.post-list {
  list-style: none;
  padding: 0;
  margin: 8px 0 0;
}
.post {
  padding: 28px 0;
  border-bottom: 1px solid var(--border);
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
