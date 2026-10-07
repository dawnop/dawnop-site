<script setup>
import { ref, watch, computed } from 'vue'
import { Search, Document, ArrowRight, Warning } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'
import { listPastes, errorText } from '../../api/pasteApi'
import { fmtDateTime, fmtBytes } from '../../utils/format'
const route = useRoute(),
  router = useRouter()
const search = ref(''),
  items = ref([]),
  total = ref(0),
  loading = ref(false),
  error = ref('')
const page = ref(1)
const query = computed(() => (typeof route.query.q === 'string' ? route.query.q : ''))
function excerpt(text) {
  return text
    .replace(/^\s*(?:#{1,6}|>|[-*+] |```[^\n]*)/gm, '')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[*_`]/g, '')
    .trim()
}
function rowTitle(item) {
  return (
    item.title ||
    excerpt(item.summary)
      .split('\n')
      .find((line) => line.trim()) ||
    item.id
  )
}
let revision = 0
async function load() {
  const ticket = ++revision
  loading.value = true
  error.value = ''
  const q = typeof route.query.q === 'string' ? route.query.q : ''
  search.value = q
  page.value = Math.max(1, Math.min(1000000, Number(route.query.page) || 1))
  try {
    const result = await listPastes({ q, page: page.value, size: 20 })
    if (ticket !== revision) return
    items.value = result.items
    total.value = result.total
  } catch (e) {
    if (ticket === revision) error.value = errorText(e)
  } finally {
    if (ticket === revision) loading.value = false
  }
}
function navigate(nextPage = 1) {
  router.push({
    path: '/paste',
    query: {
      ...(search.value ? { q: search.value } : {}),
      ...(nextPage > 1 ? { page: nextPage } : {}),
    },
  })
}
function clearSearch() {
  search.value = ''
  navigate()
}
watch(() => route.fullPath, load, { immediate: true })
</script>
<template>
  <section class="paste-index">
    <div class="paste-index-head">
      <div class="paste-index-heading">
        <h1 class="tool-heading">{{ query ? '搜索结果' : '公开记录' }}</h1>
        <span v-if="!loading && !error" class="paste-meta">{{ total }} 条</span>
      </div>
      <form class="paste-search" role="search" @submit.prevent="navigate()">
        <el-input
          v-model="search"
          placeholder="搜索内容"
          maxlength="120"
          clearable
          aria-label="搜索 Paste"
          :prefix-icon="Search"
        />
        <el-button native-type="submit" aria-label="搜索" :icon="ArrowRight" />
      </form>
    </div>
    <div v-if="error" class="paste-state tool-state tool-panel" role="alert">
      <el-icon><Warning /></el-icon>
      <p>{{ error }}</p>
      <el-button @click="load">重试</el-button>
    </div>
    <div v-else-if="loading" class="paste-skeleton" aria-label="加载中" aria-busy="true">
      <el-skeleton v-for="n in 4" :key="n" animated>
        <template #template
          ><el-skeleton-item variant="h3" /><el-skeleton-item variant="text" /><el-skeleton-item
            variant="text"
        /></template>
      </el-skeleton>
    </div>
    <div v-else-if="!items.length" class="paste-state tool-state tool-panel">
      <el-icon><component :is="query ? Search : Document" /></el-icon>
      <h2>{{ query ? '没有找到内容' : '还没有内容' }}</h2>
      <el-button v-if="query" @click="clearSearch">清除搜索</el-button>
      <RouterLink v-else to="/paste/new"
        >新建 Paste <el-icon><ArrowRight /></el-icon
      ></RouterLink>
    </div>
    <ul v-else class="paste-list tool-panel">
      <li v-for="item in items" :key="item.id">
        <RouterLink :to="`/paste/${item.id}`" class="paste-row">
          <div class="paste-row-main">
            <h2>{{ rowTitle(item) }}</h2>
            <p>{{ excerpt(item.summary) }}</p>
            <div class="paste-row-meta paste-meta">
              <time :datetime="new Date(item.created_at * 1000).toISOString()">{{
                fmtDateTime(item.created_at * 1000)
              }}</time>
              <span>{{ fmtBytes(item.size_bytes) }}</span>
              <span v-if="!item.expires_at">永久</span>
            </div>
          </div>
          <el-icon class="paste-row-arrow"><ArrowRight /></el-icon>
        </RouterLink>
      </li>
    </ul>
    <el-pagination
      v-if="!error && !loading && total > 20"
      class="paste-pagination"
      background
      :total="total"
      :current-page="page"
      :page-size="20"
      :pager-count="5"
      layout="prev, pager, next"
      @current-change="navigate"
    />
  </section>
</template>
<style scoped>
.paste-index {
  width: 100%;
}
.paste-index-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 24px;
  margin-bottom: var(--tool-heading-gap);
}
.paste-index-heading {
  display: flex;
  align-items: baseline;
  gap: 14px;
  flex-shrink: 0;
}
.paste-search {
  display: flex;
  align-items: center;
  width: 300px;
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: var(--el-border-radius-base);
}
.paste-search:focus-within {
  border-color: var(--accent);
}
.paste-search :deep(.el-input__wrapper) {
  box-shadow: none;
  background: transparent;
  padding-left: 12px;
}
.paste-search .el-button {
  border: 0;
  margin: 3px;
  width: 30px;
  padding: 0;
  color: var(--muted);
}
.paste-search .el-button:hover {
  color: var(--accent);
  background: var(--el-color-primary-light-9);
}
.paste-list {
  list-style: none;
  padding: 0 var(--tool-panel-padding);
  margin: 0;
}
.paste-list > li:last-child {
  border-bottom: 0;
}
.paste-row {
  display: flex;
  gap: 24px;
  align-items: center;
  padding: 26px 12px;
  margin: 0 -12px;
  color: var(--fg);
  text-decoration: none;
  transition: background 0.15s;
}
.paste-list > li {
  border-bottom: 1px solid var(--border);
}
.paste-row:hover {
  background: var(--el-fill-color-lighter);
}
.paste-row-main {
  flex: 1;
  min-width: 0;
}
.paste-row h2 {
  margin: 0;
  font-size: 19px;
  font-weight: 600;
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.paste-row:hover h2 {
  color: var(--accent);
}
.paste-row p {
  margin: 8px 0 14px;
  font-size: 14px;
  color: var(--muted);
  line-height: 1.7;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  overflow-wrap: anywhere;
}
.paste-row-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
}
.paste-row-arrow {
  color: var(--el-text-color-placeholder);
  flex: none;
}
.paste-row:hover .paste-row-arrow {
  color: var(--accent);
}
.paste-skeleton .el-skeleton {
  padding: 26px 0;
  border-top: 1px solid var(--border);
}
.paste-skeleton :deep(.el-skeleton__h3) {
  width: 40%;
  margin-bottom: 16px;
}
.paste-skeleton :deep(.el-skeleton__text:last-child) {
  width: 25%;
  margin-top: 12px;
}
.paste-state a {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.paste-pagination {
  justify-content: center;
  margin-top: 28px;
}
@media (max-width: 600px) {
  .paste-pagination :deep(.el-pager li),
  .paste-pagination :deep(.btn-prev),
  .paste-pagination :deep(.btn-next) {
    min-width: 28px;
    margin: 0 2px;
  }
  .paste-index-head {
    align-items: stretch;
    flex-direction: column;
    gap: 20px;
    margin-bottom: 24px;
  }
  .paste-search {
    width: 100%;
  }
  .paste-row {
    padding-top: 22px;
    padding-bottom: 22px;
    gap: 16px;
  }
  .paste-row h2 {
    font-size: 18px;
  }
}
</style>
