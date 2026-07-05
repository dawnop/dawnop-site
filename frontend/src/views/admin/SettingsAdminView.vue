<script setup>
// 后台全局配置：key-value 存后端 settings 表，此页只是薄壳。
// 新增配置项：后端 api/settings.py 的 DEFAULTS/VALIDATORS 加一项，这里加一个表单控件。
import { ref, reactive, computed, onMounted } from 'vue'
import { settingsApi } from '../../api'
import { useIsMobile } from '../../composables/useIsMobile'

const isMobile = useIsMobile()
const loading = ref(true)
const saving = ref(false)
const form = reactive({
  upload_concurrency: 3,
  download_concurrency: 3,
  storage_quota_gb: 10,
  text_preview_max_kb: 512,
})
const snapshot = ref('')
const dirty = computed(() => JSON.stringify(form) !== snapshot.value)

async function load() {
  loading.value = true
  try {
    Object.assign(form, (await settingsApi.get()).data)
    snapshot.value = JSON.stringify(form)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    Object.assign(form, (await settingsApi.update({ ...form })).data)
    snapshot.value = JSON.stringify(form)
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <el-button type="primary" :loading="saving" :disabled="!dirty" @click="save">保存</el-button>
    </div>
    <p class="hint">改动即时生效（文件管理页需刷新后生效）。</p>

    <el-card v-loading="loading" shadow="never">
      <el-form
        :label-width="isMobile ? 'auto' : '140px'"
        :label-position="isMobile ? 'top' : 'right'"
        class="settings-form"
      >
        <h3 class="group-title">文件管理</h3>
        <el-form-item label="上传并发数">
          <el-input-number v-model="form.upload_concurrency" :min="1" :max="8" />
          <span class="tip">同时上传的文件数（1~8）；分片大文件内部另有分片并发</span>
        </el-form-item>
        <el-form-item label="下载并发数">
          <el-input-number v-model="form.download_concurrency" :min="1" :max="8" />
          <span class="tip">批量下载时同时进行的文件数（1~8）</span>
        </el-form-item>
        <el-form-item label="存储配额 (GB)">
          <el-input-number v-model="form.storage_quota_gb" :min="1" :max="1024" />
          <span class="tip">仅侧栏用量条的分母展示；七牛标准存储免费额度 10GB</span>
        </el-form-item>
        <el-form-item label="文本预览上限 (KB)">
          <el-input-number v-model="form.text_preview_max_kb" :min="16" :max="10240" :step="64" />
          <span class="tip">超过此大小的文本文件不做在线预览/编辑，只提供下载</span>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  margin-bottom: 12px;
}
.hint {
  color: var(--muted);
  font-size: 0.88rem;
  margin: 0 0 16px;
}
.group-title {
  margin: 4px 0 18px;
  font-size: 0.95rem;
  color: var(--el-text-color-primary);
}
.settings-form {
  max-width: 640px;
}
.tip {
  margin-left: 14px;
  color: var(--muted);
  font-size: 0.85rem;
}

@media (max-width: 768px) {
  .page-head h1 {
    font-size: 1.15rem;
  }
  .tip {
    display: block;
    margin-left: 0;
    margin-top: 4px;
  }
}
</style>
