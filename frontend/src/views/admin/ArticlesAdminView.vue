<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Search, Upload, EditPen } from '@element-plus/icons-vue'
import { articlesApi, pagesApi } from '../../api'

const router = useRouter()

const items = ref([])
const total = ref(0)
const page = ref(1)
const size = 12
const loading = ref(true)
const fileInput = ref(null)
const pageMap = ref({}) // page_id -> title
const listPages = ref([]) // 文章列表页（用于分类筛选）

// 筛选条件
const fStatus = ref('') // '' | 'published' | 'draft'
const fPageId = ref('') // '' | id
const fq = ref('')

function pageName(id) {
  return id ? pageMap.value[id] || `#${id}` : '—'
}

function articleUrl(slug) {
  return `${location.origin}/article/${slug}`
}

async function load() {
  loading.value = true
  try {
    const filters = {}
    if (fStatus.value) filters.published = fStatus.value === 'published'
    if (fPageId.value) filters.page_id = Number(fPageId.value)
    if (fq.value.trim()) filters.q = fq.value.trim()
    const [arts, pages] = await Promise.all([
      articlesApi.listAll(page.value, size, filters),
      pagesApi.listAll(),
    ])
    items.value = arts.data.items
    total.value = arts.data.total
    pageMap.value = Object.fromEntries(pages.data.map((p) => [p.id, p.title]))
    listPages.value = pages.data.filter((p) => p.type === 'article_list')
  } catch (e) {
    /* 错误已由 axios 拦截器统一提示 */
  } finally {
    loading.value = false
  }
}

function applyFilters() {
  page.value = 1
  load()
}
function resetFilters() {
  fStatus.value = ''
  fPageId.value = ''
  fq.value = ''
  page.value = 1
  load()
}

async function remove(a) {
  try {
    await ElMessageBox.confirm(`确定删除《${a.title}》？该操作不可撤销。`, '删除文章', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      confirmButtonClass: 'el-button--danger',
    })
  } catch (e) {
    return // 取消
  }
  await articlesApi.remove(a.id)
  ElMessage.success('已删除')
  load()
}

async function togglePublish(a) {
  await articlesApi.update(a.id, { published: !a.published })
  ElMessage.success(a.published ? '已转为草稿' : '已发布')
  load()
}

function pickImport() {
  fileInput.value.click()
}

// 导入复用「新建文章」流程：读出 .md 文本，存入 sessionStorage，跳到新建页预填、预览后再保存
async function onImport(e) {
  const file = e.target.files[0]
  if (!file) return
  try {
    const text = await file.text()
    sessionStorage.setItem('dawnop_import_md', text)
    e.target.value = ''
    router.push('/admin/articles/new')
  } catch (err) {
    ElMessage.error('读取文件失败，请确认是 UTF-8 文本')
    e.target.value = ''
  }
}

async function exportMd(a) {
  const { data } = await articlesApi.exportMarkdown(a.id)
  const url = URL.createObjectURL(data)
  const link = document.createElement('a')
  link.href = url
  link.download = `${a.slug}.md`
  link.click()
  URL.revokeObjectURL(url)
}

function fmtDate(s) {
  return new Date(s).toLocaleDateString('zh-CN')
}

function goPage(p) {
  page.value = p
  load()
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <h1>文章管理</h1>
      <div class="actions">
        <input ref="fileInput" type="file" accept=".md,text/markdown" hidden @change="onImport" />
        <el-button :icon="Upload" @click="pickImport">导入 .md</el-button>
        <el-button type="primary" :icon="EditPen" @click="router.push('/admin/articles/new')">
          写文章
        </el-button>
      </div>
    </div>

    <el-card class="toolbar" shadow="never">
      <el-input
        v-model="fq"
        class="search"
        placeholder="搜索标题"
        :prefix-icon="Search"
        clearable
        @keyup.enter="applyFilters"
        @clear="applyFilters"
      />
      <el-radio-group v-model="fStatus" @change="applyFilters">
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button value="published">已发布</el-radio-button>
        <el-radio-button value="draft">草稿</el-radio-button>
      </el-radio-group>
      <el-select
        v-model="fPageId"
        class="cat"
        placeholder="全部分类"
        clearable
        @change="applyFilters"
      >
        <el-option v-for="p in listPages" :key="p.id" :label="p.title" :value="p.id" />
      </el-select>
      <el-button v-if="fStatus || fPageId || fq" link @click="resetFilters">重置</el-button>
    </el-card>

    <el-card shadow="never">
      <el-table v-loading="loading" :data="items" empty-text="还没有文章">
        <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
        <el-table-column label="链接" min-width="180">
          <template #default="{ row }">
            <a
              :href="articleUrl(row.slug)"
              target="_blank"
              rel="noopener"
              class="slug-link"
              :title="row.published ? '' : '草稿：仅登录后凭直链可预览，前台列表不可见'"
            >/article/{{ row.slug }}</a>
            <span v-if="!row.published" class="draft-tag">草稿预览</span>
          </template>
        </el-table-column>
        <el-table-column label="所属页面" min-width="120">
          <template #default="{ row }"><span class="muted">{{ pageName(row.page_id) }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.published ? 'success' : 'info'" size="small" effect="light">
              {{ row.published ? '已发布' : '草稿' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="120">
          <template #default="{ row }"><span class="muted">{{ fmtDate(row.updated_at) }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="router.push(`/admin/articles/${row.id}/edit`)">
              编辑
            </el-button>
            <el-button link type="primary" @click="togglePublish(row)">
              {{ row.published ? '转草稿' : '发布' }}
            </el-button>
            <el-button link type="primary" @click="exportMd(row)">导出</el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="total > size" class="pager">
        <el-pagination
          background
          layout="prev, pager, next"
          :total="total"
          :page-size="size"
          :current-page="page"
          @current-change="goPage"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
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
.toolbar {
  margin-bottom: 16px;
}
.toolbar :deep(.el-card__body) {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
}
.search {
  width: 240px;
}
.cat {
  width: 150px;
}
.muted {
  color: var(--muted);
}
.slug-link {
  font-size: 0.85rem;
  text-decoration: none;
}
.draft-tag {
  margin-left: 6px;
  font-size: 0.7rem;
  color: var(--muted);
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  padding: 0 5px;
  white-space: nowrap;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
