<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { settingsApi } from '../../api'
import * as pasteApi from '../../api/pasteAdminApi'
import { adminSettings, loadAdminSettings, applyAdminSettings } from '../../store/adminSettings'
import { useUnsavedGuard } from '../../composables/useUnsavedGuard'

const route = useRoute()
const router = useRouter()
const sections = [
  {
    key: 'admin',
    title: '后台',
    hint: '文章与 Paste 的分页，以及后台列表的显示密度。',
    fields: [
      { key: 'admin_page_size', label: '每页条数', min: 10, max: 50, unit: '条' },
      { key: 'admin_compact', label: '紧凑列表', switch: true },
    ],
  },
  {
    key: 'files',
    title: '文件',
    hint: '文件管理的传输、预览与用量统计。',
    fields: [
      { key: 'upload_concurrency', label: '上传并发', min: 1, max: 8, unit: '个文件' },
      { key: 'download_concurrency', label: '下载并发', min: 1, max: 8, unit: '个文件' },
      {
        key: 'storage_quota_gb',
        label: '存储配额',
        min: 1,
        max: 1024,
        unit: 'GB',
        tip: '用量条的总容量，按七牛资源包填写。',
      },
      {
        key: 'text_preview_max_kb',
        label: '文本预览上限',
        min: 16,
        max: 10240,
        unit: 'KiB',
        tip: '超过上限只提供下载。',
      },
    ],
  },
  {
    key: 'drop',
    title: '上传链接',
    hint: '创建链接时预填，可在创建时单独调整；已有链接保持原额度。',
    fields: [
      { key: 'drop_expiry_hours', label: '默认有效期', min: 1, max: 720, unit: '小时' },
      { key: 'drop_max_files', label: '文件数上限', min: 1, max: 1000, unit: '个' },
      { key: 'drop_file_max_mb', label: '单文件上限', min: 1, max: 5120, unit: 'MiB' },
      { key: 'drop_total_max_mb', label: '总上限', min: 1, max: 51200, unit: 'MiB' },
    ],
  },
  {
    key: 'paste',
    title: 'Paste',
    hint: '匿名公开提交的频率、正文大小与存量上限。按 UTC 日期计额。',
    fields: [
      { key: 'max_bytes', label: '单条正文', min: 1, max: 262144, factor: 1024, unit: 'KiB' },
      { key: 'source_minute', label: '单来源 / 分钟', min: 1, max: 60, unit: '条' },
      { key: 'source_day', label: '单来源 / 天', min: 1, max: 10000, unit: '条' },
      {
        key: 'source_day_bytes',
        label: '单来源正文 / 天',
        min: 1,
        max: 104857600,
        factor: 1048576,
        unit: 'MiB',
      },
      { key: 'global_day', label: '全站 / 天', min: 1, max: 100000, unit: '条' },
      {
        key: 'global_day_bytes',
        label: '全站正文 / 天',
        min: 1,
        max: 1073741824,
        factor: 1048576,
        unit: 'MiB',
      },
      { key: 'stored_count', label: '存量条数', min: 1, max: 100000, unit: '条' },
      {
        key: 'stored_bytes',
        label: '存量正文',
        min: 1,
        max: 1073741824,
        factor: 1048576,
        unit: 'MiB',
      },
    ],
  },
]
const active = computed(() => sections.find((s) => s.key === route.query.section) || sections[0])
const form = reactive({ ...adminSettings.values })
const ready = reactive({})
const errors = reactive({})
const saved = reactive({})
const saving = ref('')
const loading = reactive({})
function payload(section) {
  return Object.fromEntries(section.fields.map((f) => [f.key, form[f.key]]))
}
function dirty(section) {
  return ready[section.key] && JSON.stringify(payload(section)) !== saved[section.key]
}
// 基线始终是「无改动」；各分组单独保存，避免清掉另一组尚未保存的输入。
useUnsavedGuard(() => JSON.stringify(sections.filter(dirty).map((s) => s.key)))
function hydrate(section, values) {
  for (const field of section.fields) form[field.key] = values[field.key]
  saved[section.key] = JSON.stringify(payload(section))
  ready[section.key] = true
  errors[section.key] = false
}
async function load(paste = false) {
  const targets = sections.filter((s) => (s.key === 'paste') === paste)
  targets.forEach((s) => {
    loading[s.key] = true
    errors[s.key] = false
  })
  try {
    const values = paste ? await pasteApi.settings() : await loadAdminSettings(true)
    targets.forEach((s) => hydrate(s, values))
  } catch {
    targets.forEach((s) => {
      errors[s.key] = true
    })
  } finally {
    targets.forEach((s) => {
      loading[s.key] = false
    })
  }
}
function update(field, value) {
  form[field.key] = value == null ? null : Math.round(value * (field.factor || 1))
}
async function save(section) {
  if (saving.value || !ready[section.key] || !dirty(section)) return
  const values = payload(section)
  for (const f of section.fields) {
    const n = values[f.key]
    if (!Number.isInteger(n) || n < (f.min ?? 0) || n > (f.max ?? 1)) {
      ElMessage.error(`请填写有效的${f.label}`)
      return
    }
  }
  if (section.key === 'drop' && values.drop_total_max_mb < values.drop_file_max_mb) {
    ElMessage.error('总上限不能小于单个文件上限')
    return
  }
  saving.value = section.key
  try {
    if (section.key === 'paste') await pasteApi.saveSettings(values)
    else applyAdminSettings((await settingsApi.update(values)).data)
    saved[section.key] = JSON.stringify(values)
    ElMessage.success('已保存')
  } catch {
    // 请求失败保留输入与未保存状态。
  } finally {
    saving.value = ''
  }
}
onMounted(() => {
  load()
  load(true)
})
</script>

<template>
  <div class="settings-layout">
    <nav class="settings-nav" aria-label="设置分组">
      <button
        v-for="s in sections"
        :key="s.key"
        type="button"
        :class="{ selected: active.key === s.key }"
        :aria-current="active.key === s.key ? 'page' : undefined"
        @click="router.replace({ query: { ...route.query, section: s.key } })"
      >
        {{ s.title }}<span v-if="dirty(s)" class="dirty-dot" aria-label="未保存" />
      </button>
    </nav>
    <section class="settings-panel card" :aria-labelledby="`settings-${active.key}`">
      <header class="section-head">
        <h2 :id="`settings-${active.key}`">{{ active.title }}</h2>
        <p>{{ active.hint }}</p>
      </header>
      <div v-if="errors[active.key]" class="load-error" role="alert">
        加载失败，重试后可编辑。<el-button @click="load(active.key === 'paste')">重试</el-button>
      </div>
      <el-form
        novalidate
        v-else-if="ready[active.key]"
        v-loading="loading[active.key]"
        label-position="top"
        :disabled="!ready[active.key] || !!saving"
        @submit.prevent="save(active)"
      >
        <div class="settings-fields">
          <el-form-item v-for="field in active.fields" :key="field.key" :label="field.label">
            <el-switch
              v-if="field.switch"
              v-model="form[field.key]"
              :active-value="1"
              :inactive-value="0"
              :aria-label="field.label"
            />
            <template v-else>
              <div class="number-field">
                <el-input-number
                  :model-value="
                    form[field.key] == null ? undefined : form[field.key] / (field.factor || 1)
                  "
                  :min="field.min / (field.factor || 1)"
                  :max="field.max / (field.factor || 1)"
                  :precision="field.factor ? undefined : 0"
                  :aria-label="field.label"
                  controls-position="right"
                  @update:model-value="update(field, $event)"
                />
                <span class="unit">{{ field.unit }}</span>
              </div>
              <p v-if="field.tip" class="field-note">{{ field.tip }}</p>
            </template>
          </el-form-item>
        </div>
        <footer class="settings-footer">
          <span class="save-state">{{
            dirty(active) ? '未保存' : ready[active.key] ? '已保存' : '加载中'
          }}</span>
          <el-button
            :disabled="!dirty(active) || !!saving"
            @click="hydrate(active, JSON.parse(saved[active.key]))"
            >还原</el-button
          >
          <el-button
            type="primary"
            native-type="submit"
            :loading="saving === active.key"
            :disabled="!dirty(active) || !!saving"
            >保存</el-button
          >
        </footer>
      </el-form>
      <div v-else class="settings-loading" role="status" aria-label="加载设置">
        <el-skeleton :rows="4" animated />
      </div>
    </section>
  </div>
</template>

<style scoped>
.settings-layout {
  display: grid;
  grid-template-columns: 160px minmax(0, 760px);
  gap: 24px;
  align-items: start;
}
.settings-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.settings-nav button {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  text-align: left;
  font: inherit;
  font-size: 14px;
  padding: 12px 16px;
  cursor: pointer;
}
.settings-nav button:hover {
  background: #eef2f7;
}
.settings-nav button.selected {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}
.settings-nav button:focus-visible {
  outline: 2px solid var(--accent);
}
.dirty-dot {
  width: 5px;
  height: 5px;
  background: var(--accent);
  border-radius: 50%;
}
.section-head {
  border-bottom: 1px solid var(--border);
  padding-bottom: 20px;
  margin-bottom: 24px;
}
.section-head h2 {
  margin: 0 0 8px;
  font-size: 16px;
  font-weight: 600;
}
.section-head p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
}
.settings-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 32px;
}
.settings-fields :deep(.el-form-item__content) {
  display: block;
}
.settings-fields :deep(.el-form-item__label) {
  font-size: 13px;
}
.number-field {
  display: flex;
  align-items: center;
  gap: 10px;
}
.number-field .el-input-number {
  width: 156px;
  max-width: 100%;
}
.unit {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
}
.field-note {
  margin: 6px 0 0;
  color: var(--muted);
  line-height: 1.6;
  font-size: 12px;
}
.settings-footer {
  border-top: 1px solid var(--border);
  padding-top: 20px;
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.settings-footer .el-button + .el-button {
  margin-left: 0;
}
.save-state {
  font-size: 12px;
  color: var(--muted);
  margin-right: auto;
}
.load-error {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  font-size: 13px;
  color: var(--muted);
}
@media (max-width: 1100px) {
  .settings-layout {
    grid-template-columns: 120px minmax(0, 1fr);
    gap: 16px;
  }
  .settings-fields {
    column-gap: 16px;
  }
}
@media (max-width: 900px) {
  .settings-fields {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 768px) {
  .settings-layout {
    grid-template-columns: minmax(0, 1fr);
    gap: 16px;
  }
  .settings-nav {
    flex-direction: row;
  }
  .settings-nav button {
    flex: 1;
    justify-content: center;
    padding: 10px 4px;
    white-space: nowrap;
    font-size: 13px;
  }
  .settings-fields {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .number-field {
    flex-wrap: wrap;
    gap: 4px;
  }
  .number-field .el-input-number {
    width: 100%;
  }
}
@media (max-width: 390px) {
  .settings-fields {
    grid-template-columns: minmax(0, 1fr);
  }
  .number-field {
    flex-wrap: nowrap;
  }
  .number-field .el-input-number {
    width: 156px;
  }
}
</style>
