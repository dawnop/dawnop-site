<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as api from '../../api/pasteAdminApi'
import { useIsMobile } from '../../composables/useIsMobile'
import { adminSettings, loadAdminSettings } from '../../store/adminSettings'
import ListPager from '../../components/ListPager.vue'
import MarkdownDocument from '../../components/MarkdownDocument.vue'
import { fmtBytes, fmtDateTime } from '../../utils/format'
const items = ref([]),
  total = ref(0),
  page = ref(1),
  selection = ref([]),
  loading = ref(false),
  busy = ref(false),
  error = ref(false)
const search = ref(''),
  status = ref(''),
  dates = ref(null),
  counts = ref({ count: 0, bytes: 0, expired: 0 })
const view = ref(null),
  mode = ref('预览')
const isMobile = useIsMobile()
const size = computed(() => adminSettings.values.admin_page_size)
const mobileIds = computed({
  get: () => selection.value.map((row) => row.id),
  set: (ids) => {
    selection.value = items.value.filter((row) => ids.includes(row.id))
  },
})
const allSelected = computed({
  get: () => items.value.length > 0 && selection.value.length === items.value.length,
  set: (value) => {
    selection.value = value ? [...items.value] : []
  },
})
function title(row) {
  return row.title || row.summary.split('\n').find((line) => line.trim()) || row.id
}
let revision = 0
async function load() {
  const ticket = ++revision
  loading.value = true
  error.value = false
  selection.value = []
  try {
    await loadAdminSettings().catch(() => {})
    const [result, stats] = await Promise.all([
      api.list({
        page: page.value,
        size: size.value,
        q: search.value,
        status: status.value,
        ...(dates.value
          ? {
              after: Math.floor(dates.value[0] / 1000),
              before: Math.floor(dates.value[1] / 1000) + 86399,
            }
          : {}),
      }),
      api.stats(),
    ])
    if (ticket !== revision) return
    items.value = result.items
    total.value = result.total
    counts.value = stats
    if (!items.value.length && page.value > 1) {
      page.value--
      return load()
    }
  } catch {
    if (ticket === revision) error.value = true
  } finally {
    if (ticket === revision) loading.value = false
  }
}
function filter() {
  page.value = 1
  load()
}
async function show(item) {
  try {
    const result = await api.detail(item.id)
    mode.value = '预览'
    view.value = result
  } catch {
    /* 统一提示 */
  }
}
async function action(operation, message) {
  if (busy.value) return
  try {
    await ElMessageBox.confirm(message, 'Paste', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
    busy.value = true
    await operation()
    ElMessage.success('已完成')
    await load()
  } catch {
    /* 取消或统一错误提示 */
  } finally {
    busy.value = false
  }
}
function remove(ids) {
  action(() => api.remove(ids), `删除 ${ids.length} 条 Paste？`)
}
watch(size, () => {
  page.value = 1
})
function goPage(value) {
  page.value = value
  load()
}
onMounted(load)
</script>
<template>
  <div class="pastes-admin">
    <el-card class="toolbar-card" shadow="never"
      ><div class="admin-toolbar">
        <el-input
          v-model="search"
          placeholder="搜索"
          maxlength="120"
          clearable
          @keyup.enter="filter"
        /><el-select v-model="status" placeholder="全部" @change="filter"
          ><el-option label="全部" value="" /><el-option label="未到期" value="active" /><el-option
            label="已到期"
            value="expired" /><el-option label="永久" value="permanent" /></el-select
        ><el-date-picker
          v-model="dates"
          type="daterange"
          start-placeholder="创建起日"
          end-placeholder="创建止日"
          @change="filter"
        /><el-button @click="filter">搜索</el-button
        ><router-link to="/admin/settings?section=paste" class="settings-link"
          >提交额度</router-link
        >
      </div>
      <div class="admin-toolbar">
        <span class="stats"
          >{{ counts.count }} 条 · {{ fmtBytes(counts.bytes) }} · {{ counts.expired }} 条到期</span
        ><el-button
          :disabled="!selection.length || busy"
          @click="remove(selection.map((row) => row.id))"
          >删除所选</el-button
        ><el-button
          :disabled="!counts.expired || busy"
          @click="action(api.cleanup, '清理所有到期 Paste？')"
          >清理到期</el-button
        >
      </div>
    </el-card>
    <el-card class="list-card" shadow="never">
      <p v-if="error" role="alert">加载失败 <el-button text @click="load">重试</el-button></p>
      <el-table
        v-else-if="!isMobile"
        border
        v-loading="loading"
        :data="items"
        row-key="id"
        @selection-change="selection = $event"
      >
        <el-table-column type="selection" width="44" />
        <el-table-column label="正文" min-width="220"
          ><template #default="{ row }"
            ><el-button link type="primary" class="paste-name" @click="show(row)">{{
              title(row)
            }}</el-button></template
          ></el-table-column
        >
        <el-table-column label="创建" width="165"
          ><template #default="{ row }">{{
            fmtDateTime(row.created_at * 1000)
          }}</template></el-table-column
        >
        <el-table-column label="到期" width="165"
          ><template #default="{ row }">{{
            row.expires_at ? fmtDateTime(row.expires_at * 1000) : '永久'
          }}</template></el-table-column
        >
        <el-table-column label="大小" width="90"
          ><template #default="{ row }">{{ fmtBytes(row.size_bytes) }}</template></el-table-column
        >
        <el-table-column label="操作" width="150"
          ><template #default="{ row }"
            ><el-button
              v-if="row.expires_at"
              link
              type="primary"
              :disabled="busy"
              @click="action(() => api.retain(row.id), '永久保留此 Paste？')"
              >保留</el-button
            ><el-button link :disabled="busy" @click="remove([row.id])">删除</el-button></template
          ></el-table-column
        >
      </el-table>
      <div v-else v-loading="loading" class="mobile-pastes">
        <el-checkbox
          v-if="items.length"
          v-model="allSelected"
          :indeterminate="selection.length > 0 && !allSelected"
          >全选本页</el-checkbox
        >
        <el-empty v-if="!items.length && !loading" description="没有 Paste" :image-size="64" />
        <el-checkbox-group v-model="mobileIds">
          <article v-for="row in items" :key="row.id" class="mobile-paste">
            <div class="mobile-title">
              <el-checkbox :value="row.id" :aria-label="`选择 ${title(row)}`" /><el-button
                link
                type="primary"
                class="paste-name"
                @click="show(row)"
                >{{ title(row) }}</el-button
              >
            </div>
            <p class="mobile-meta">
              {{ fmtDateTime(row.created_at * 1000) }} · {{ fmtBytes(row.size_bytes) }}
            </p>
            <div class="mobile-bottom">
              <span>{{
                row.expires_at ? `到期 ${fmtDateTime(row.expires_at * 1000)}` : '永久'
              }}</span>
              <div>
                <el-button
                  v-if="row.expires_at"
                  link
                  type="primary"
                  :disabled="busy"
                  @click="action(() => api.retain(row.id), '永久保留此 Paste？')"
                  >保留</el-button
                ><el-button link :disabled="busy" @click="remove([row.id])">删除</el-button>
              </div>
            </div>
          </article>
        </el-checkbox-group>
      </div>
      <ListPager
        :current-page="page"
        :page-size="size"
        :total="total"
        variant="admin"
        :small="isMobile"
        @change="goPage"
      />
    </el-card>
    <el-dialog
      :model-value="!!view"
      :title="view?.title || 'Paste'"
      width="min(880px, 94vw)"
      @close="view = null"
      ><template v-if="view"
        ><el-radio-group v-model="mode" size="small"
          ><el-radio-button label="预览" /><el-radio-button label="原文" /></el-radio-group
        ><MarkdownDocument v-if="mode === '预览'" :source="view.content" class="detail" />
        <pre v-else class="source">{{ view.content }}</pre>
      </template></el-dialog
    >
  </div>
</template>
<style scoped>
.admin-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
}
.admin-toolbar .el-input {
  width: 220px;
}
.admin-toolbar .el-select {
  width: 110px;
}
.admin-toolbar .el-date-editor {
  max-width: 280px;
  width: 280px;
  flex: 0 1 280px;
}
.stats {
  color: var(--muted);
  margin-right: auto;
  font-size: 13px;
}
.paste-name {
  display: block;
  max-width: 100%;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.detail,
.source {
  margin-top: 24px;
}
.source {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font:
    13px/1.8 ui-monospace,
    monospace;
}
.el-pagination {
  margin-top: 20px;
  justify-content: flex-end;
}

.toolbar-card {
  margin-bottom: 16px;
}
.admin-toolbar:last-child {
  margin-bottom: 0;
}
.admin-toolbar .el-button + .el-button {
  margin-left: 0;
}
.settings-link {
  color: var(--accent);
  font-size: 13px;
  text-decoration: none;
  margin-left: auto;
}
.list-card :deep(.el-card__body) {
  padding: 16px;
}
.mobile-paste {
  border-bottom: 1px solid var(--border);
  padding: 16px 0;
}
.mobile-paste:last-child {
  border-bottom: 0;
}
.mobile-title {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.mobile-title .el-checkbox {
  margin-right: 0;
}
.mobile-title .paste-name {
  flex: 1;
  text-align: left;
  min-width: 0;
  font-weight: 500;
}
.mobile-meta {
  font-size: 12px;
  color: var(--muted);
  margin: 6px 0 8px 26px;
}
.mobile-bottom {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
}
.detail,
.source {
  max-height: 65dvh;
  overflow-y: auto;
}
@media (max-width: 768px) {
  .admin-toolbar {
    gap: 8px;
  }
  .admin-toolbar .el-input {
    width: auto;
    flex: 1;
    min-width: 140px;
  }
  .admin-toolbar .el-select {
    width: 110px;
  }
  .admin-toolbar .el-date-editor {
    width: 100%;
    max-width: 100%;
    flex: 1 0 100%;
    min-width: 0;
  }
  .stats {
    width: 100%;
  }
  .settings-link {
    padding: 8px 0;
  }
  .toolbar-card :deep(.el-card__body) {
    padding: 14px;
  }
}
</style>
