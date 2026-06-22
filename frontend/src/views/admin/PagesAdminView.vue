<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { pagesApi } from '../../api'

const router = useRouter()
const pages = ref([])
const loading = ref(true)
const error = ref('')
const counts = ref({}) // page.id -> 文章数（仅文章列表页）

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await pagesApi.listAll()
    pages.value = data
    loadCounts()
  } catch (e) {
    error.value = '加载失败，请确认后端已启动并已登录。'
  } finally {
    loading.value = false
  }
}

// 文章列表页：查该栏目下文章数（页面通常很少，逐个查可接受）
async function loadCounts() {
  const next = {}
  for (const p of pages.value) {
    if (p.type !== 'article_list') continue
    try {
      const { data } = await pagesApi.listArticles(p.slug, 1, 1)
      next[p.id] = data.total
    } catch (e) {
      /* 忽略单页失败 */
    }
  }
  counts.value = next
}

function newPage() {
  router.push('/admin/pages/new')
}
function edit(p) {
  router.push(`/admin/pages/${p.id}/edit`)
}

async function remove(p) {
  if (!confirm(`确定删除页面「${p.title}」？其下文章会解除归属。`)) return
  await pagesApi.remove(p.id)
  load()
}

function fmtDate(s) {
  return new Date(s).toLocaleDateString('zh-CN')
}

// 拖拽排序
const dragIndex = ref(null)
function onDragStart(i) {
  dragIndex.value = i
}
async function onDrop(i) {
  const from = dragIndex.value
  dragIndex.value = null
  if (from === null || from === i) return
  const arr = pages.value.slice()
  const [moved] = arr.splice(from, 1)
  arr.splice(i, 0, moved)
  pages.value = arr
  const { data } = await pagesApi.reorder(arr.map((p) => p.id))
  pages.value = data
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <h1>页面管理</h1>
      <button class="primary" @click="newPage">新建页面</button>
    </div>
    <p class="hint">拖动行首 ⠿ 调整导航顺序；首页固定在最前，不在此列。</p>

    <div class="card">
      <p v-if="error" class="error">{{ error }}</p>
      <p v-else-if="loading" class="muted">加载中…</p>
      <p v-else-if="pages.length === 0" class="empty">还没有页面，点右上角「新建页面」。</p>
      <table v-else class="data-grid">
        <thead>
          <tr>
            <th class="drag-col"></th>
            <th>标题</th>
            <th>类型</th>
            <th>路径</th>
            <th>文章数</th>
            <th>导航</th>
            <th>更新于</th>
            <th class="ops">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(p, i) in pages"
            :key="p.id"
            :class="{ dragging: dragIndex === i }"
            @dragover.prevent
            @drop="onDrop(i)"
          >
            <td
              class="drag-handle"
              draggable="true"
              title="拖动排序"
              @dragstart="onDragStart(i)"
            >
              ⠿
            </td>
            <td>{{ p.title }}</td>
            <td class="muted">{{ p.type === 'content' ? '内容页' : '文章列表' }}</td>
            <td class="muted">/p/{{ p.slug }}</td>
            <td class="muted">
              {{ p.type === 'article_list' ? (counts[p.id] ?? '…') : '—' }}
            </td>
            <td>
              <span :class="['badge', p.nav_visible ? 'on' : 'off']">
                {{ p.nav_visible ? '显示' : '隐藏' }}
              </span>
            </td>
            <td class="muted">{{ fmtDate(p.updated_at) }}</td>
            <td class="ops">
              <a @click.prevent="edit(p)">编辑</a>
              <a class="danger" @click.prevent="remove(p)">删除</a>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.hint {
  margin: -8px 0 16px;
}
.drag-col {
  width: 28px;
}
.drag-handle {
  cursor: grab;
  color: #c9cdd4;
  text-align: center;
  user-select: none;
}
.drag-handle:active {
  cursor: grabbing;
}
.dragging {
  opacity: 0.5;
}
</style>
