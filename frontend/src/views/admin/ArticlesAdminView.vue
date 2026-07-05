<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Search, Upload, EditPen, MoreFilled } from '@element-plus/icons-vue'
import { articlesApi, pagesApi, tagsApi } from '../../api'
import { parseFrontmatter } from '../../utils/frontmatter'
import { useColWidths } from '../../utils/colWidths'
import { useIsMobile } from '../../composables/useIsMobile'

const { colW, onHeaderDrag } = useColWidths('dawnop_colw_articles')

const router = useRouter()
const isMobile = useIsMobile()

// 移动卡片的「更多」操作
function rowCmd(cmd, row) {
  if (cmd === 'publish') togglePublish(row)
  else if (cmd === 'export') exportMd(row)
  else if (cmd === 'delete') remove(row)
}

const items = ref([])
const total = ref(0)
const page = ref(1)
const size = 12
const loading = ref(true)
const fileInput = ref(null)
const pageMap = ref({}) // page_id -> title
const listPages = ref([]) // 文章列表页（用于分类筛选）
const allTags = ref([]) // 已有标签名，供内联编辑补全

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
    const [arts, pages, tags] = await Promise.all([
      articlesApi.listAll(page.value, size, filters),
      pagesApi.listAll(),
      tagsApi.listAll(),
    ])
    // 给每行挂一个 tagNames（标签名数组），供内联 el-select 双向绑定
    items.value = arts.data.items.map((a) => ({
      ...a,
      tagNames: (a.tags || []).map((t) => t.name),
    }))
    total.value = arts.data.total
    pageMap.value = Object.fromEntries(pages.data.map((p) => [p.id, p.title]))
    listPages.value = pages.data.filter((p) => p.type === 'article_list')
    allTags.value = tags.data.map((t) => t.name)
  } catch (e) {
    /* 错误已由 axios 拦截器统一提示 */
  } finally {
    loading.value = false
  }
}

// 内联改标签：改完即存；成功后刷新补全候选（可能新建了标签）
async function saveTags(row) {
  try {
    await articlesApi.update(row.id, { tags: row.tagNames })
    ElMessage.success('标签已更新')
    const { data } = await tagsApi.listAll()
    allTags.value = data.map((t) => t.name)
  } catch (e) {
    load() // 失败回滚显示
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
    const { meta, body } = parseFrontmatter(text)
    sessionStorage.setItem('dawnop_import_md', body)
    sessionStorage.setItem('dawnop_import_meta', JSON.stringify(meta))
    sessionStorage.setItem('dawnop_import_name', file.name)
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
      <div class="tb-actions">
        <input ref="fileInput" type="file" accept=".md,text/markdown" hidden @change="onImport" />
        <el-button :icon="Upload" @click="pickImport">导入 .md</el-button>
        <el-button type="primary" :icon="EditPen" @click="router.push('/admin/articles/new')">
          写文章
        </el-button>
      </div>
    </el-card>

    <el-card v-if="!isMobile" shadow="never">
      <el-table v-loading="loading" :data="items" border empty-text="还没有文章" @header-dragend="onHeaderDrag">
        <el-table-column prop="title" label="标题" :width="colW.title" min-width="150" show-overflow-tooltip />
        <el-table-column label="链接" :width="colW['链接']" min-width="110" show-overflow-tooltip>
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
        <el-table-column label="所属页面" :width="colW['所属页面']" min-width="90">
          <template #default="{ row }"><span class="muted">{{ pageName(row.page_id) }}</span></template>
        </el-table-column>
        <el-table-column label="标签" :width="colW['标签']" min-width="150">
          <template #default="{ row }">
            <el-select
              v-model="row.tagNames"
              multiple
              filterable
              allow-create
              default-first-option
              collapse-tags
              collapse-tags-tooltip
              :reserve-keyword="false"
              size="small"
              placeholder="加标签"
              class="tag-cell"
              @change="saveTags(row)"
            >
              <el-option v-for="t in allTags" :key="t" :label="t" :value="t" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="状态" :width="colW['状态'] || 80">
          <template #default="{ row }">
            <el-tag :type="row.published ? 'success' : 'info'" size="small" effect="light">
              {{ row.published ? '已发布' : '草稿' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="views" label="浏览量" :width="colW.views || 84" sortable>
          <template #default="{ row }"><span class="muted">{{ row.views ?? 0 }}</span></template>
        </el-table-column>
        <el-table-column label="更新时间" :width="colW['更新时间'] || 100">
          <template #default="{ row }"><span class="muted">{{ fmtDate(row.updated_at) }}</span></template>
        </el-table-column>
        <el-table-column label="操作" :width="colW['操作'] || 210" fixed="right">
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

    <!-- 移动端卡片列表 -->
    <div v-else v-loading="loading" class="m-list">
      <el-empty v-if="!items.length && !loading" description="还没有文章" :image-size="80" />
      <el-card v-for="row in items" :key="row.id" shadow="never" class="m-card">
        <div class="m-card-head">
          <a :href="articleUrl(row.slug)" target="_blank" rel="noopener" class="m-title">{{ row.title }}</a>
          <el-tag :type="row.published ? 'success' : 'info'" size="small" effect="light">
            {{ row.published ? '已发布' : '草稿' }}
          </el-tag>
        </div>
        <div class="m-slug">/article/{{ row.slug }}</div>
        <div class="m-meta">
          <span>{{ pageName(row.page_id) }}</span>
          <span>· {{ row.views ?? 0 }} 浏览</span>
          <span>· {{ fmtDate(row.updated_at) }}</span>
        </div>
        <el-select
          v-model="row.tagNames"
          multiple
          filterable
          allow-create
          default-first-option
          collapse-tags
          collapse-tags-tooltip
          :reserve-keyword="false"
          size="small"
          placeholder="加标签"
          class="m-tags"
          @change="saveTags(row)"
        >
          <el-option v-for="t in allTags" :key="t" :label="t" :value="t" />
        </el-select>
        <div class="m-actions">
          <el-button size="small" @click="router.push(`/admin/articles/${row.id}/edit`)">编辑</el-button>
          <el-dropdown trigger="click" @command="(c) => rowCmd(c, row)">
            <el-button size="small" :icon="MoreFilled" />
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="publish">{{ row.published ? '转草稿' : '发布' }}</el-dropdown-item>
                <el-dropdown-item command="export">导出 .md</el-dropdown-item>
                <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-card>
      <div v-if="total > size" class="pager">
        <el-pagination
          background
          small
          layout="prev, pager, next"
          :total="total"
          :page-size="size"
          :current-page="page"
          @current-change="goPage"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.toolbar {
  margin-bottom: 16px;
}
.toolbar :deep(.el-card__body) {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
}
/* 操作按钮靠右，与左侧筛选同处一行 */
.tb-actions {
  margin-left: auto;
  display: flex;
  gap: 10px;
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
.tag-cell {
  width: 100%;
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
  line-height: 1.35;
  text-decoration: none;
  color: var(--el-text-color-primary);
}
.m-slug {
  font-size: 0.8rem;
  color: var(--muted);
  word-break: break-all;
}
.m-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 0.8rem;
  color: var(--muted);
}
.m-tags {
  width: 100%;
}
.m-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 2px;
}
.m-actions > .el-button {
  flex: 1;
}

/* 移动端工具条 / 头部堆叠 */
@media (max-width: 768px) {
  .toolbar :deep(.el-card__body) {
    flex-wrap: wrap;
    gap: 10px;
    padding: 12px;
  }
  .tb-actions {
    width: 100%;
    margin-left: 0;
    order: -1; /* 移动端操作按钮放最上 */
  }
  .tb-actions .el-button {
    flex: 1;
  }
  .search,
  .cat {
    width: 100%;
  }
  .toolbar :deep(.el-radio-group) {
    display: flex;
    width: 100%;
  }
  .toolbar :deep(.el-radio-button) {
    flex: 1;
  }
  .toolbar :deep(.el-radio-button__inner) {
    width: 100%;
  }
}
</style>
