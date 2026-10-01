<script setup>
// 「创建上传链接」对话框：先填表（标签 / 有效期 / 文件数 / 单文件上限 / 总上限），
// 创建成功后同一对话框切到结果页，展示分享链接与 curl 示例。明文 token 只在这次响应里出现，
// 关掉对话框即丢弃，之后无处可查（后端只存哈希）。
import { ref, reactive, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { CopyDocument } from '@element-plus/icons-vue'
import { fmApi } from '../api'
import { useIsMobile } from '../composables/useIsMobile'

const props = defineProps({
  // 目标目录的相对路径，根为空串
  dir: { type: String, default: '' },
})
const show = defineModel({ type: Boolean, default: false })

const isMobile = useIsMobile()

const MB = 1024 * 1024
const GB = 1024 * MB
// 上下限与后端一致（越界后端回 422），前端先拦一遍给即时提示
const LIMITS = {
  max_files: [1, 1000],
  max_file_bytes: [1, 5 * GB],
  max_total_bytes: [1, 50 * GB],
}
const expiryOptions = [
  { label: '1 小时', value: 3600 },
  { label: '1 天', value: 86400 },
  { label: '7 天', value: 7 * 86400 },
  { label: '30 天', value: 30 * 86400 },
]
const unitOptions = [
  { label: 'MB', value: MB },
  { label: 'GB', value: GB },
]

const formRef = ref(null)
const form = reactive({})
function resetForm() {
  Object.assign(form, {
    label: '',
    expires_in_s: 86400,
    max_files: 20,
    fileSize: 100,
    fileUnit: MB,
    totalSize: 1,
    totalUnit: GB,
  })
}
resetForm()

const busy = ref(false)
const result = ref(null) // 创建成功后的响应（含明文 token）

const dirLabel = computed(() => (props.dir ? `/${props.dir}` : '/（根目录）'))
const bytesOf = (n, unit) => Math.round(Number(n) * unit)

function rangeRule(key, toBytes) {
  const [lo, hi] = LIMITS[key]
  return {
    validator: (_r, _v, cb) => {
      const v = toBytes()
      if (!Number.isFinite(v) || v < lo) return cb(new Error('请填写大于 0 的数'))
      if (v > hi) {
        return cb(new Error(`不能超过 ${key === 'max_files' ? hi : `${hi / GB} GB`}`))
      }
      cb()
    },
    trigger: 'change',
  }
}
const rules = {
  label: [{ max: 100, message: '标签最多 100 个字', trigger: 'blur' }],
  max_files: [rangeRule('max_files', () => Number(form.max_files))],
  fileSize: [rangeRule('max_file_bytes', () => bytesOf(form.fileSize, form.fileUnit))],
  totalSize: [
    rangeRule('max_total_bytes', () => bytesOf(form.totalSize, form.totalUnit)),
    {
      validator: (_r, _v, cb) =>
        bytesOf(form.totalSize, form.totalUnit) < bytesOf(form.fileSize, form.fileUnit)
          ? cb(new Error('总上限不能小于单个文件上限'))
          : cb(),
      trigger: 'change',
    },
  ],
}

async function submit() {
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  busy.value = true
  try {
    result.value = await fmApi.createDrop(props.dir, {
      label: form.label.trim(),
      expires_in_s: form.expires_in_s,
      max_files: Number(form.max_files),
      max_file_bytes: bytesOf(form.fileSize, form.fileUnit),
      max_total_bytes: bytesOf(form.totalSize, form.totalUnit),
    })
  } catch {
    // 失败提示由 axios 拦截器统一给（含 422 的 detail）
  } finally {
    busy.value = false
  }
}

const link = computed(() => (result.value ? `${location.origin}/drop#${result.value.token}` : ''))
const curl = computed(() =>
  result.value
    ? `curl -T ./example.pdf -H "X-Drop-Token: ${result.value.token}" ${location.origin}/api/drop/files/example.pdf`
    : '',
)

// 复制：优先 Clipboard API（要求安全上下文），不可用时退回选中 + execCommand
async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    ta.remove()
    if (!ok) return ElMessage.error('复制失败，请手动选中复制')
  }
  ElMessage.success('已复制')
}

// 每次打开都从空白表单开始；关闭时丢弃明文 token
watch(show, (v) => {
  if (v) {
    resetForm()
    result.value = null
    formRef.value?.clearValidate()
  } else {
    result.value = null
  }
})
</script>

<template>
  <el-dialog
    v-model="show"
    :title="result ? '上传链接已创建' : '创建上传链接'"
    :width="isMobile ? '94%' : '520px'"
    :close-on-click-modal="!result"
    append-to-body
  >
    <!-- 表单 -->
    <template v-if="!result">
      <p class="hint">
        持有链接的任何人都能往 <b>{{ dirLabel }}</b> 里新增文件（不能查看、覆盖或删除已有文件），
        直到过期、被吊销或额度用完。
      </p>
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        :label-position="isMobile ? 'top' : 'right'"
        label-width="96px"
        @submit.prevent="submit"
      >
        <el-form-item label="标签" prop="label">
          <el-input
            v-model="form.label"
            maxlength="100"
            placeholder="可选，访客会在上传页看到，如「项目资料收集」"
          />
        </el-form-item>
        <el-form-item label="有效期">
          <el-radio-group v-model="form.expires_in_s">
            <el-radio-button v-for="o in expiryOptions" :key="o.value" :value="o.value">
              {{ o.label }}
            </el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="文件数上限" prop="max_files">
          <el-input-number
            v-model="form.max_files"
            :min="1"
            :max="LIMITS.max_files[1]"
            :step="1"
            step-strictly
            controls-position="right"
          />
        </el-form-item>
        <el-form-item label="单文件上限" prop="fileSize">
          <div class="size-row">
            <el-input-number
              v-model="form.fileSize"
              :min="0"
              :precision="0"
              controls-position="right"
            />
            <el-select v-model="form.fileUnit" class="unit">
              <el-option
                v-for="u in unitOptions"
                :key="u.label"
                :label="u.label"
                :value="u.value"
              />
            </el-select>
          </div>
        </el-form-item>
        <el-form-item label="总上限" prop="totalSize">
          <div class="size-row">
            <el-input-number
              v-model="form.totalSize"
              :min="0"
              :precision="0"
              controls-position="right"
            />
            <el-select v-model="form.totalUnit" class="unit">
              <el-option
                v-for="u in unitOptions"
                :key="u.label"
                :label="u.label"
                :value="u.value"
              />
            </el-select>
          </div>
        </el-form-item>
      </el-form>
    </template>

    <!-- 结果 -->
    <template v-else>
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="链接只显示这一次"
        description="关闭本窗口后无法再次查看，请现在复制保存。之后可在「上传链接」页吊销或删除。"
        class="once"
      />
      <div class="out">
        <div class="out-label">网页上传链接</div>
        <div class="out-row">
          <el-input :model-value="link" readonly @focus="$event.target.select()" />
          <el-button :icon="CopyDocument" @click="copy(link)">复制</el-button>
        </div>
      </div>
      <div class="out">
        <div class="out-label">命令行上传（curl）</div>
        <div class="out-row top">
          <el-input
            :model-value="curl"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 5 }"
            readonly
            class="mono"
            @focus="$event.target.select()"
          />
          <el-button :icon="CopyDocument" @click="copy(curl)">复制</el-button>
        </div>
        <p class="out-note">
          把 <code>./example.pdf</code> 换成本地文件，URL 末尾换成保存时的文件名。文件名含中文、
          空格或特殊字符时须先做 URL 编码（如 <code>报告.pdf</code> 写作
          <code>%E6%8A%A5%E5%91%8A.pdf</code>）。每次一个文件，同名不会覆盖，会自动改名。
        </p>
        <p class="out-note">curl 方式单个文件不超过 512 MB，更大的文件请用网页上传。</p>
      </div>
    </template>

    <template #footer>
      <template v-if="!result">
        <el-button @click="show = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="submit">创建</el-button>
      </template>
      <template v-else>
        <router-link to="/admin/drops" class="manage" @click="show = false"
          >管理上传链接</router-link
        >
        <el-button type="primary" @click="show = false">我已保存，关闭</el-button>
      </template>
    </template>
  </el-dialog>
</template>

<style scoped>
.hint {
  margin: 0 0 16px;
  color: #4e5969;
  line-height: 1.7;
  font-size: 0.9rem;
}
.size-row {
  display: flex;
  gap: 8px;
}
.unit {
  width: 84px;
}
.once {
  margin-bottom: 16px;
}
.out + .out {
  margin-top: 16px;
}
.out-label {
  font-size: 0.85rem;
  color: #4e5969;
  margin-bottom: 6px;
}
.out-row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.out-row.top {
  align-items: flex-start;
}
.mono :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.8rem;
  word-break: break-all;
}
.out-note {
  margin: 6px 0 0;
  font-size: 0.8rem;
  color: #8c8c8c;
  line-height: 1.7;
}
.out-note code {
  background: #f0f1f3;
  padding: 0 4px;
  border-radius: 4px;
}
.manage {
  margin-right: 12px;
  font-size: 0.9rem;
  text-decoration: none;
}
</style>
