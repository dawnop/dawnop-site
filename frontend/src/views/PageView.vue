<script setup>
import { ref, watch } from 'vue'
import { useRoute, RouterLink } from 'vue-router'
import { pagesApi } from '../api'
import MarkdownView from '../components/MarkdownView.vue'

const route = useRoute()
const page = ref(null)
const loading = ref(true)
const notFound = ref(false)

// 列表页文章
const items = ref([])
const total = ref(0)
const pageNum = ref(1)
const size = 10

async function loadArticles(slug) {
  const { data } = await pagesApi.listArticles(slug, pageNum.value, size)
  items.value = data.items
  total.value = data.total
}

function setMeta(desc) {
  let m = document.querySelector('meta[name="description"]')
  if (!m) {
    m = document.createElement('meta')
    m.setAttribute('name', 'description')
    document.head.appendChild(m)
  }
  m.setAttribute('content', desc || '')
}

async function load(slug) {
  loading.value = true
  notFound.value = false
  page.value = null
  pageNum.value = 1
  try {
    const { data } = await pagesApi.get(slug)
    page.value = data
    document.title = `${data.title} · dawnop`
    setMeta(data.description)
    if (data.type === 'article_list') await loadArticles(slug)
  } catch (e) {
    notFound.value = true
  } finally {
    loading.value = false
  }
}

function go(p) {
  pageNum.value = p
  loadArticles(route.params.slug)
}

function fmtDate(s) {
  return new Date(s).toLocaleDateString('zh-CN')
}

watch(() => route.params.slug, (slug) => slug && load(slug), { immediate: true })
</script>

<template>
  <el-skeleton v-if="loading" :rows="6" animated />
  <el-result
    v-else-if="notFound"
    icon="warning"
    title="页面不存在"
    sub-title="该页面可能已被删除或地址有误"
  >
    <template #extra>
      <el-button type="primary" @click="$router.push('/')">返回首页</el-button>
    </template>
  </el-result>

  <template v-else-if="page">
    <h1 class="page-title">{{ page.title }}</h1>
    <p v-if="page.description" class="page-desc">{{ page.description }}</p>

    <!-- 内容页 -->
    <MarkdownView v-if="page.type === 'content'" :source="page.content" />

    <!-- 文章列表页 -->
    <template v-else>
      <el-empty v-if="items.length === 0" description="该栏目下还没有文章" />
      <ul v-else class="post-list">
        <li v-for="a in items" :key="a.id" class="post">
          <RouterLink :to="`/article/${a.slug}`" class="post-title">{{ a.title }}</RouterLink>
          <div class="post-meta">{{ fmtDate(a.created_at) }}</div>
          <p v-if="a.summary" class="post-summary">{{ a.summary }}</p>
        </li>
      </ul>
      <div v-if="total > size" class="pager">
        <el-pagination
          background
          layout="prev, pager, next"
          :total="total"
          :page-size="size"
          :current-page="pageNum"
          @current-change="go"
        />
      </div>
    </template>
  </template>
</template>

<style scoped>
.page-title {
  font-size: 1.8rem;
  margin: 0 0 20px;
}
.page-desc {
  margin: -12px 0 24px;
  color: var(--muted);
  font-size: 1.02rem;
  line-height: 1.6;
}
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
.pager {
  display: flex;
  justify-content: center;
  margin-top: 28px;
}
</style>
