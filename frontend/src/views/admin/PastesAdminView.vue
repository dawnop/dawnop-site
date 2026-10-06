<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as api from '../../api/pasteAdminApi'
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
  mode = ref('预览'),
  settingsOpen = ref(false),
  limits = reactive({})
const labels = {
  max_bytes: '单条正文 / 字节',
  source_minute: '单来源 / 分钟',
  source_day: '单来源 / 天',
  source_day_bytes: '单来源 / 天 / 字节',
  global_day: '全站 / 天',
  global_day_bytes: '全站 / 天 / 字节',
  stored_count: '存量条数',
  stored_bytes: '存量正文 / 字节',
}
const ceilings = {
  max_bytes: 262144,
  source_minute: 60,
  source_day: 10000,
  source_day_bytes: 104857600,
  global_day: 100000,
  global_day_bytes: 1073741824,
  stored_count: 100000,
  stored_bytes: 1073741824,
}
let revision = 0
async function load() {
  const ticket = ++revision
  loading.value = true
  error.value = false
  selection.value = []
  try {
    const [result, stats] = await Promise.all([
      api.list({
        page: page.value,
        size: 20,
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
async function openSettings() {
  try {
    Object.assign(limits, await api.settings())
    settingsOpen.value = true
  } catch {
    /* 统一提示 */
  }
}
async function saveSettings() {
  if (busy.value) return
  busy.value = true
  try {
    await api.saveSettings(limits)
    settingsOpen.value = false
    ElMessage.success('已保存')
  } catch {
    /* 保留输入 */
  } finally {
    busy.value = false
  }
}
onMounted(load)
</script>
<template>
  <div class="pastes-admin">
    <div class="admin-toolbar">
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
      /><el-button @click="filter">搜索</el-button><el-button @click="openSettings">额度</el-button>
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
    <p v-if="error">加载失败 <el-button text @click="load">重试</el-button></p>
    <el-table
      v-else
      v-loading="loading"
      :data="items"
      row-key="id"
      @selection-change="selection = $event"
    >
      <el-table-column type="selection" width="44" />
      <el-table-column label="正文" min-width="220"
        ><template #default="{ row }"
          ><el-button link type="primary" class="paste-name" @click="show(row)">{{
            row.title || row.summary.split('\n').find((line) => line.trim()) || row.id
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
    <el-pagination
      v-if="total > 20"
      v-model:current-page="page"
      :page-size="20"
      :total="total"
      layout="prev, pager, next"
      @current-change="load"
    />
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
    <el-dialog v-model="settingsOpen" title="提交额度" width="min(520px, 94vw)"
      ><el-form label-width="170px"
        ><el-form-item v-for="(label, key) in labels" :key="key" :label="label"
          ><el-input-number
            v-model="limits[key]"
            :min="1"
            :max="ceilings[key]"
            :precision="0"
            :disabled="busy" /></el-form-item></el-form
      ><template #footer
        ><el-button :disabled="busy" @click="settingsOpen = false">取消</el-button
        ><el-button type="primary" :loading="busy" @click="saveSettings">保存</el-button></template
      ></el-dialog
    >
  </div>
</template>
<style scoped>
.admin-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 20px;
}
.admin-toolbar .el-input {
  width: 220px;
}
.admin-toolbar .el-select {
  width: 110px;
}
.admin-toolbar .el-date-editor {
  max-width: 280px;
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
</style>
