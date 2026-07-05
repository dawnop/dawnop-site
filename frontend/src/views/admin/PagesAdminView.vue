<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { Rank, Document, Collection, Top, Bottom } from '@element-plus/icons-vue'
import Sortable from 'sortablejs'
import { pagesApi } from '../../api'
import { useColWidths } from '../../utils/colWidths'
import { useIsMobile } from '../../composables/useIsMobile'

const { colW, onHeaderDrag } = useColWidths('dawnop_colw_pages')

const router = useRouter()
const isMobile = useIsMobile()

// 移动端排序：上/下移一位（拖拽在触屏不好用，改按钮）
async function movePage(index, dir) {
  const j = index + dir
  if (j < 0 || j >= pages.value.length) return
  const arr = pages.value.slice()
  ;[arr[index], arr[j]] = [arr[j], arr[index]]
  pages.value = arr
  try {
    const { data } = await pagesApi.reorder(arr.map((p) => p.id))
    pages.value = data
    ElMessage.success('顺序已保存')
  } catch (e) {
    load()
  }
}
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

const TYPE_LABELS = { content: '内容页', article_list: '文章列表', builtin: '内置' }

// 内置页（首页/标签页）：只能改导航名与显隐，不可删、无内容可编辑
async function renameBuiltin(p) {
  let value
  try {
    ;({ value } = await ElMessageBox.prompt('导航中显示的名称', `重命名「${p.title}」`, {
      inputValue: p.title,
      inputValidator: (v) => (v && v.trim() ? true : '名称不能为空'),
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    }))
  } catch (e) {
    return // 取消
  }
  if (value.trim() === p.title) return
  await pagesApi.update(p.id, { title: value.trim() })
  ElMessage.success('已重命名')
  load()
}

async function toggleNav(p) {
  await pagesApi.update(p.id, { nav_visible: !p.nav_visible })
  ElMessage.success(p.nav_visible ? '已从导航隐藏' : '已在导航显示')
  load()
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
  if (!isMobile.value) initSortable() // 移动端用上/下按钮排序，不挂拖拽
})
</script>

<template>
  <div>
    <div class="page-head">
      <div class="actions">
        <el-button :icon="Document" @click="newPage('content')">新建内容页</el-button>
        <el-button type="primary" :icon="Collection" @click="newPage('article_list')">
          新建文章列表页
        </el-button>
      </div>
    </div>
    <p class="hint">
      {{ isMobile ? '用卡片上的 ↑↓ 调整导航顺序' : '拖动行首图标调整导航顺序' }}；「首页」「标签」为内置页，可改名、隐藏，不可删除。
    </p>

    <el-card v-if="!isMobile" shadow="never">
      <el-table ref="tableRef" v-loading="loading" :data="pages" row-key="id" border empty-text="还没有页面" @header-dragend="onHeaderDrag">
        <el-table-column width="48">
          <template #default>
            <el-icon class="drag-handle" title="拖动排序"><Rank /></el-icon>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="标题" :width="colW.title || 240" show-overflow-tooltip />
        <el-table-column label="类型" :width="colW['类型'] || 96">
          <template #default="{ row }">
            <span class="muted">{{ TYPE_LABELS[row.type] || row.type }}</span>
          </template>
        </el-table-column>
        <el-table-column label="路径" :width="colW['路径'] || 180" show-overflow-tooltip>
          <template #default="{ row }"><span class="muted">{{ row.path }}</span></template>
        </el-table-column>
        <el-table-column label="文章数" :width="colW['文章数'] || 90">
          <template #default="{ row }">
            <span class="muted">{{ row.type === 'article_list' ? (counts[row.id] ?? '…') : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="导航" :width="colW['导航'] || 90">
          <template #default="{ row }">
            <el-tag :type="row.nav_visible ? 'success' : 'info'" size="small" effect="light">
              {{ row.nav_visible ? '显示' : '隐藏' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="更新于" :width="colW['更新于'] || 110">
          <template #default="{ row }"><span class="muted">{{ fmtDate(row.updated_at) }}</span></template>
        </el-table-column>
        <el-table-column />
        <el-table-column label="操作" :width="colW['操作'] || 150" fixed="right">
          <template #default="{ row }">
            <template v-if="row.type === 'builtin'">
              <el-button link type="primary" @click="renameBuiltin(row)">重命名</el-button>
              <el-button link type="primary" @click="toggleNav(row)">
                {{ row.nav_visible ? '隐藏' : '显示' }}
              </el-button>
            </template>
            <template v-else>
              <el-button link type="primary" @click="router.push(`/admin/pages/${row.id}/edit`)">
                编辑
              </el-button>
              <el-button link type="danger" @click="remove(row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 移动端卡片列表 -->
    <div v-else v-loading="loading" class="m-list">
      <el-empty v-if="!pages.length && !loading" description="还没有页面" :image-size="80" />
      <el-card v-for="(row, i) in pages" :key="row.id" shadow="never" class="m-card">
        <div class="m-card-head">
          <span class="m-title">{{ row.title }}</span>
          <el-tag :type="row.nav_visible ? 'success' : 'info'" size="small" effect="light">
            {{ row.nav_visible ? '导航显示' : '导航隐藏' }}
          </el-tag>
        </div>
        <div class="m-meta">
          <span>{{ TYPE_LABELS[row.type] || row.type }}</span>
          <span v-if="row.path">· {{ row.path }}</span>
          <span v-if="row.type === 'article_list'">· {{ counts[row.id] ?? '…' }} 篇</span>
          <span>· {{ fmtDate(row.updated_at) }}</span>
        </div>
        <div class="m-actions">
          <el-button-group>
            <el-button size="small" :icon="Top" :disabled="i === 0" @click="movePage(i, -1)" />
            <el-button size="small" :icon="Bottom" :disabled="i === pages.length - 1" @click="movePage(i, 1)" />
          </el-button-group>
          <span class="m-spacer" />
          <template v-if="row.type === 'builtin'">
            <el-button size="small" @click="renameBuiltin(row)">重命名</el-button>
            <el-button size="small" @click="toggleNav(row)">{{ row.nav_visible ? '隐藏' : '显示' }}</el-button>
          </template>
          <template v-else>
            <el-button size="small" @click="router.push(`/admin/pages/${row.id}/edit`)">编辑</el-button>
            <el-button size="small" type="danger" plain @click="remove(row)">删除</el-button>
          </template>
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  margin-bottom: 12px;
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

/* 移动端卡片列表 */
.m-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.m-card :deep(.el-card__body) {
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.m-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.m-title {
  font-weight: 600;
  font-size: 0.98rem;
}
.m-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 0.8rem;
  color: var(--muted);
}
.m-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.m-spacer {
  flex: 1;
}

@media (max-width: 768px) {
  .page-head {
    flex-wrap: wrap;
    gap: 10px;
  }
  .page-head h1 {
    font-size: 1.15rem;
  }
  .actions {
    width: 100%;
  }
  .actions .el-button {
    flex: 1;
  }
}
</style>
