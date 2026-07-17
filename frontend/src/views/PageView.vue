<script setup>
import { ref, watch, computed } from 'vue'
import { useRoute } from 'vue-router'
import { pagesApi } from '../api'
import MarkdownView from '../components/MarkdownView.vue'
import PostList from '../components/PostList.vue'
import { stripFirstH1 } from '../utils/markdownTitle'
import { fmtDate } from '../utils/format'

const route = useRoute()
const page = ref(null)
const loading = ref(true)
const notFound = ref(false)

// 标题取自正文 H1 时，渲染前剥离那行，避免与页面标题重复（存储仍是完整原文）
const displayContent = computed(() => {
  if (!page.value) return ''
  return page.value.auto_title ? stripFirstH1(page.value.content) : page.value.content
})

// 列表页文章
const items = ref([])
const total = ref(0)
const latestDate = ref(null)
const pageNum = ref(1)
const size = 10

async function loadArticles(slug) {
  const { data } = await pagesApi.listArticles(slug, pageNum.value, size)
  items.value = data.items
  total.value = data.total
  // 最近更新取全列表最新一篇（首页第一条即最新）；翻页时不覆盖，保持稳定
  if (pageNum.value === 1) latestDate.value = data.items[0]?.created_at || null
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

watch(
  () => route.params.slug,
  (slug) => slug && load(slug),
  { immediate: true },
)
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
    <!-- 内容页 -->
    <template v-if="page.type === 'content'">
      <h1 class="page-title">{{ page.title }}</h1>
      <p v-if="page.description" class="page-desc">{{ page.description }}</p>
      <MarkdownView :source="displayContent" />
    </template>

    <!-- 文章列表页 -->
    <template v-else>
      <header class="list-header">
        <h1 class="list-title">{{ page.title }}</h1>
        <p v-if="page.description" class="list-subtitle">{{ page.description }}</p>
        <p v-if="total" class="list-meta">
          共 {{ total }} 篇<template v-if="latestDate">
            · 最近更新 {{ fmtDate(latestDate) }}</template
          >
        </p>
      </header>
      <el-empty v-if="items.length === 0" description="该栏目下还没有文章" />
      <PostList v-else :items="items" />
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
.list-header {
  margin: 8px 0 8px;
  padding-bottom: 28px;
  border-bottom: 1px solid var(--border);
}
.list-title {
  margin: 0;
  font-size: 2.1rem;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.2;
  color: var(--fg);
}
.list-subtitle {
  margin: 14px 0 0;
  max-width: 44em;
  color: var(--muted);
  font-size: 1.05rem;
  line-height: 1.7;
}
.list-meta {
  margin: 14px 0 0;
  color: #8b949e;
  font-size: 0.85rem;
}
@media (max-width: 640px) {
  .list-title {
    font-size: 1.7rem;
  }
  .list-subtitle {
    font-size: 1rem;
  }
}
.pager {
  display: flex;
  justify-content: center;
  margin-top: 28px;
}
</style>
