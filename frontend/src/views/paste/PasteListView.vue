<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { listPastes, errorText } from '../../api/pasteApi'
import { fmtDateTime } from '../../utils/format'
const route = useRoute(),
  router = useRouter()
const search = ref(''),
  items = ref([]),
  total = ref(0),
  loading = ref(false),
  error = ref('')
const page = ref(1)
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
watch(() => route.fullPath, load, { immediate: true })
</script>
<template>
  <form class="paste-toolbar" @submit.prevent="navigate()">
    <el-input
      v-model="search"
      placeholder="搜索"
      maxlength="120"
      clearable
      aria-label="搜索 Paste"
    /><el-button native-type="submit">搜索</el-button>
  </form>
  <div v-if="error" class="paste-error">
    {{ error }} <el-button text @click="load">重试</el-button>
  </div>
  <div v-else v-loading="loading" class="paste-list">
    <RouterLink v-for="item in items" :key="item.id" :to="`/paste/${item.id}`" class="paste-row"
      ><span class="paste-row-title">{{
        item.title || item.summary.split('\n').find((line) => line.trim()) || item.id
      }}</span
      ><time class="paste-meta">{{ fmtDateTime(item.created_at * 1000) }}</time>
      <p v-if="item.title">{{ item.summary }}</p></RouterLink
    >
    <p v-if="!loading && !items.length" class="paste-meta">暂无内容</p>
  </div>
  <el-pagination
    v-if="!error && total > 20"
    :current-page="page"
    :page-size="20"
    :total="total"
    layout="prev, pager, next"
    @current-change="navigate"
  />
</template>
<style scoped>
.paste-toolbar .el-input {
  flex: 1;
}
.paste-list {
  min-height: 120px;
}
.paste-row {
  display: block;
  padding: 20px 0;
  border-bottom: 1px solid var(--border);
  text-decoration: none;
  color: var(--fg);
}
.paste-row-title {
  font-size: 16px;
  display: block;
  margin-bottom: 8px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.paste-row:hover .paste-row-title {
  color: var(--accent);
}
.paste-row p {
  color: var(--muted);
  margin: 10px 0 0;
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
