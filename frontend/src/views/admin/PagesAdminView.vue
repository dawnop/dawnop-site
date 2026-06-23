<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Rank, Document, Collection } from '@element-plus/icons-vue'
import Sortable from 'sortablejs'
import { pagesApi } from '../../api'

const router = useRouter()
const pages = ref([])
const loading = ref(true)
const counts = ref({}) // page.id -> 文章数（仅文章列表页）
const tableRef = ref(null)
let sortable = null

async function load() {
  loading.value = true
  try {
    const { data } = await pagesApi.listAll()
    pages.value = data
    loadCounts()
  } catch (e) {
    /* 拦截器已提示 */
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

function newPage(type) {
  router.push({ path: '/admin/pages/new', query: { type } })
}

async function remove(p) {
  try {
    await ElMessageBox.confirm(
      `确定删除页面「${p.title}」？其下文章会解除归属。`,
      '删除页面',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消', confirmButtonClass: 'el-button--danger' },
    )
  } catch (e) {
    return
  }
  await pagesApi.remove(p.id)
  ElMessage.success('已删除')
  load()
}

function fmtDate(s) {
  return new Date(s).toLocaleDateString('zh-CN')
}

// 行拖拽排序：sortablejs 绑定到 el-table 内部 tbody，拖完同步数据并提交顺序
function initSortable() {
  const el = tableRef.value?.$el?.querySelector('.el-table__body-wrapper tbody')
  if (!el || sortable) return
  sortable = Sortable.create(el, {
    handle: '.drag-handle',
    animation: 180,
    ghostClass: 'drag-ghost',
    onEnd: async ({ oldIndex, newIndex }) => {
      if (oldIndex === newIndex) return
      const arr = pages.value.slice()
      const [moved] = arr.splice(oldIndex, 1)
      arr.splice(newIndex, 0, moved)
      pages.value = arr
      try {
        const { data } = await pagesApi.reorder(arr.map((p) => p.id))
        pages.value = data
        ElMessage.success('顺序已保存')
      } catch (e) {
        load()
      }
    },
  })
}

onMounted(async () => {
  await load()
  await nextTick()
  initSortable()
})
</script>

<template>
  <div>
    <div class="page-head">
      <h1>页面管理</h1>
      <div class="actions">
        <el-button :icon="Document" @click="newPage('content')">新建内容页</el-button>
        <el-button type="primary" :icon="Collection" @click="newPage('article_list')">
          新建文章列表页
        </el-button>
      </div>
    </div>
    <p class="hint">拖动行首图标调整导航顺序；首页固定在最前，不在此列。</p>

    <el-card shadow="never">
      <el-table ref="tableRef" v-loading="loading" :data="pages" row-key="id" empty-text="还没有页面">
        <el-table-column width="48">
          <template #default>
            <el-icon class="drag-handle" title="拖动排序"><Rank /></el-icon>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="标题" min-width="160" show-overflow-tooltip />
        <el-table-column label="类型" width="110">
          <template #default="{ row }">
            <span class="muted">{{ row.type === 'content' ? '内容页' : '文章列表' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="路径" min-width="140">
          <template #default="{ row }"><span class="muted">/p/{{ row.slug }}</span></template>
        </el-table-column>
        <el-table-column label="文章数" width="90">
          <template #default="{ row }">
            <span class="muted">{{ row.type === 'article_list' ? (counts[row.id] ?? '…') : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="导航" width="90">
          <template #default="{ row }">
            <el-tag :type="row.nav_visible ? 'success' : 'info'" size="small" effect="light">
              {{ row.nav_visible ? '显示' : '隐藏' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="更新于" width="120">
          <template #default="{ row }"><span class="muted">{{ fmtDate(row.updated_at) }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="router.push(`/admin/pages/${row.id}/edit`)">
              编辑
            </el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.page-head h1 {
  margin: 0;
  font-size: 1.3rem;
  font-weight: 600;
}
.actions {
  display: flex;
  gap: 10px;
}
.hint {
  margin: 0 0 16px;
  color: var(--muted);
  font-size: 0.85rem;
}
.muted {
  color: var(--muted);
}
.drag-handle {
  cursor: grab;
  color: #c0c4cc;
}
.drag-handle:active {
  cursor: grabbing;
}
.drag-ghost {
  opacity: 0.5;
  background: var(--accent-soft);
}
</style>
