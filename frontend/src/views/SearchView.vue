<script setup>
import { ref, watch } from 'vue'
import { fmtDate } from '../utils/format'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { Search } from '@element-plus/icons-vue'
import { searchApi } from '../api'

const route = useRoute()
const router = useRouter()

const kw = ref(route.query.q || '') // 输入框
const items = ref([])
const total = ref(0)
const page = ref(1)
const size = 10
const loading = ref(false)
const searched = ref(false) // 是否已发起过一次查询（区分「初始空」与「无结果」）

function readMinutes(wc) {
  return Math.max(1, Math.round((wc || 0) / 300))
}

// 提交：把关键词写进 URL（可分享/前进后退），由 watch 触发实际查询
function submit() {
  const q = kw.value.trim()
  if (!q) return
  router.push({ name: 'search', query: { q } })
}

async function load() {
  const q = (route.query.q || '').trim()
  kw.value = route.query.q || ''
  if (!q) {
    items.value = []
    total.value = 0
    searched.value = false
    return
  }
  loading.value = true
  searched.value = true
  try {
    const { data } = await searchApi.query(q, page.value, size)
    items.value = data.items
    total.value = data.total
    document.title = `“${q}” 的搜索结果 · dawnop`
  } catch (e) {
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function go(p) {
  page.value = p
  load()
}

watch(
  () => route.query.q,
  () => {
    page.value = 1
    load()
  },
  { immediate: true },
)
</script>

<template>
  <div class="search-page">
    <form class="search-box" @submit.prevent="submit">
      <el-input
        v-model="kw"
        size="large"
        placeholder="搜索文章标题、摘要、正文…"
        :prefix-icon="Search"
        clearable
        @keyup.enter="submit"
      />
    </form>

    <p v-if="searched && !loading" class="count">
      找到 {{ total }} 条结果<span v-if="route.query.q">，关键词「{{ route.query.q }}」</span>
    </p>

    <el-skeleton v-if="loading" :rows="4" animated />

    <el-empty v-else-if="!searched" description="输入关键词开始搜索" />
    <el-empty v-else-if="items.length === 0" description="没有找到匹配的文章，换个关键词试试" />

    <ul v-else class="result-list">
      <li v-for="a in items" :key="a.id" class="result">
        <RouterLink :to="`/article/${a.slug}`" class="r-title">
          <span v-html="a.title_html"></span>
        </RouterLink>
        <p v-if="a.excerpt_html" class="r-excerpt" v-html="a.excerpt_html"></p>
        <div class="r-meta">
          <span>{{ fmtDate(a.created_at) }}</span>
          <span class="dot">·</span>
          <span>约 {{ readMinutes(a.word_count) }} 分钟</span>
          <template v-if="a.tags && a.tags.length">
            <span class="dot">·</span>
            <RouterLink v-for="t in a.tags" :key="t.slug" :to="`/tag/${t.slug}`" class="r-tag"
              >#{{ t.name }}</RouterLink
            >
          </template>
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
.search-box {
  margin: 4px 0 18px;
}
.count {
  margin: 0 0 18px;
  color: var(--muted);
  font-size: 0.9rem;
}
.result-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.result {
  padding: 18px 0;
  border-bottom: 1px solid #f0f1f3;
}
.result:first-child {
  padding-top: 0;
}
.r-title {
  font-size: 1.15rem;
  font-weight: 600;
  color: #1a1a1a;
  text-decoration: none;
  line-height: 1.4;
}
.r-title:hover {
  color: var(--accent);
}
.r-excerpt {
  margin: 7px 0 0;
  color: #5a5a5a;
  font-size: 0.92rem;
  line-height: 1.65;
}
.r-meta {
  margin-top: 9px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px;
  color: var(--muted);
  font-size: 0.82rem;
}
.r-meta .dot {
  color: #c8ccd1;
}
.r-tag {
  color: var(--accent);
  text-decoration: none;
}
.r-tag:hover {
  text-decoration: underline;
}
.pager {
  display: flex;
  justify-content: center;
  margin-top: 28px;
}
/* 高亮命中（title_html / excerpt_html 里的 <mark>）*/
.search-page :deep(mark) {
  background: #fff1b8;
  color: inherit;
  padding: 0 1px;
  border-radius: 2px;
}
</style>
