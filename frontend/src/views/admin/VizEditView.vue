<script setup>
import { ref, onMounted, onBeforeUnmount, computed, watch, nextTick, createApp } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { vizApi } from '../../api'
import { compileSfc } from '../../viz/compileSfc'
import { buildComponent, acquireStyle } from '../../viz/runtime'
import { invalidateViz } from '../../viz/registry'

const route = useRoute()
const router = useRouter()

const id = computed(() => route.params.id)
const isEdit = computed(() => !!id.value)

const STARTER = `<script setup>
import { ref, computed } from 'vue'

const n = ref(3)
const bars = computed(() => [...Array(n.value).keys()])
<\/script>

<template>
  <div class="demo">
    <div class="row">
      <button @click="n = Math.max(1, n - 1)">－</button>
      <span class="n">{{ n }}</span>
      <button @click="n++">＋</button>
    </div>
    <div class="bars">
      <div v-for="i in bars" :key="i" class="bar" :style="{ height: 18 + i * 14 + 'px' }" />
    </div>
    <p class="cap">点 ＋ / － 改变柱子数量（这是一个最小可交互示例）。</p>
  </div>
</template>

<style scoped>
.demo { font-family: -apple-system, 'PingFang SC', sans-serif; }
.row { display: flex; align-items: center; gap: 12px; }
.row button {
  width: 32px; height: 32px; font-size: 18px; cursor: pointer;
  border: 1px solid #d0d7de; border-radius: 6px; background: #fff;
}
.n { font-size: 1.1rem; font-weight: 600; min-width: 24px; text-align: center; }
.bars { display: flex; align-items: flex-end; gap: 8px; height: 120px; margin-top: 14px; }
.bar { width: 26px; background: #1677ff; border-radius: 4px 4px 0 0; transition: height .3s; }
.cap { color: #6b7280; font-size: .85rem; margin-top: 10px; }
</style>
`

const form = ref({ slug: '', name: '', source: STARTER })
const saving = ref(false)
const compileError = ref('')
const lastBuilt = ref(null) // { compiled, style }，最近一次成功编译的产物

const rules = {
  slug: [
    { required: true, message: '请输入标识', trigger: 'blur' },
    { pattern: /^[a-z0-9]+(?:-[a-z0-9]+)*$/, message: '只能用小写字母、数字、连字符', trigger: 'blur' },
  ],
}
const formRef = ref(null)

// ---- 实时预览 ----
const previewHost = ref(null)
let previewApp = null
let styleRelease = () => {}
let compileTimer = null

function teardownPreview() {
  if (previewApp) {
    try {
      previewApp.unmount()
    } catch (e) {
      /* 忽略 */
    }
    previewApp = null
  }
  styleRelease()
  styleRelease = () => {}
}

async function recompile() {
  compileError.value = ''
  let built
  try {
    built = await compileSfc(form.value.source, form.value.slug || 'preview')
  } catch (e) {
    compileError.value = e?.message || String(e)
    lastBuilt.value = null
    return
  }
  lastBuilt.value = built
  await nextTick()
  teardownPreview()
  const host = previewHost.value
  if (!host) return
  try {
    const component = buildComponent(built.compiled)
    styleRelease = acquireStyle(form.value.slug || 'preview', built.style)
    previewApp = createApp(component)
    previewApp.mount(host)
  } catch (e) {
    compileError.value = '运行预览失败：' + (e?.message || String(e))
  }
}

// 源码/标识变化 → 防抖重编译预览
watch(
  () => [form.value.source, form.value.slug],
  () => {
    clearTimeout(compileTimer)
    compileTimer = setTimeout(recompile, 400)
  }
)

// ---- 未保存离开拦截 ----
let snapshot = ''
let justSaved = false
const serialize = () => JSON.stringify(form.value)
const dirty = computed(() => serialize() !== snapshot)
function beforeUnload(e) {
  if (dirty.value && !justSaved) {
    e.preventDefault()
    e.returnValue = ''
  }
}

onMounted(async () => {
  if (isEdit.value) {
    try {
      const { data } = await vizApi.listAll()
      const v = data.find((x) => String(x.id) === String(id.value))
      if (!v) {
        ElMessage.error('组件不存在')
        router.replace('/admin/viz')
        return
      }
      form.value = { slug: v.slug, name: v.name || '', source: v.source || '' }
    } catch (e) {
      return
    }
  }
  snapshot = serialize()
  window.addEventListener('beforeunload', beforeUnload)
  recompile()
})

onBeforeUnmount(() => {
  clearTimeout(compileTimer)
  teardownPreview()
  window.removeEventListener('beforeunload', beforeUnload)
})

onBeforeRouteLeave(async () => {
  if (!dirty.value || justSaved) return true
  try {
    await ElMessageBox.confirm('有未保存的修改，确定离开吗？', '放弃修改', {
      type: 'warning',
      confirmButtonText: '放弃',
      cancelButtonText: '继续编辑',
    })
    return true
  } catch (e) {
    return false
  }
})

async function save() {
  try {
    await formRef.value.validate()
  } catch (e) {
    ElMessage.warning('请完善必填项')
    return
  }
  // 保存前确保最新源码能编译（用最近一次成功产物；若有错则拦下）
  await recompile()
  if (compileError.value || !lastBuilt.value) {
    ElMessage.error('编译未通过，请先修正预览中的错误')
    return
  }
  saving.value = true
  try {
    const payload = {
      slug: form.value.slug,
      name: form.value.name,
      source: form.value.source,
      compiled: lastBuilt.value.compiled,
      style: lastBuilt.value.style,
    }
    if (isEdit.value) {
      await vizApi.update(id.value, payload)
    } else {
      await vizApi.create(payload)
    }
    invalidateViz(form.value.slug) // 使文章预览/同会话下次渲染拿到新版本
    justSaved = true
    ElMessage.success('已保存')
    router.push('/admin/viz')
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="viz-edit">
    <div class="topbar">
      <h1>{{ isEdit ? '编辑可视化' : '新建可视化' }}</h1>
      <div class="actions">
        <el-button @click="router.push('/admin/viz')">返回</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </div>
    </div>

    <el-form ref="formRef" :model="form" :rules="rules" inline class="meta">
      <el-form-item label="标识" prop="slug">
        <el-input v-model="form.slug" placeholder="如 my-viz" style="width: 220px" />
      </el-form-item>
      <el-form-item label="名称">
        <el-input v-model="form.name" placeholder="便于识别的名字（可选）" style="width: 260px" />
      </el-form-item>
      <span class="usage">
        文章里用 <code>```viz {{ form.slug || 'my-viz' }}```</code> 引用
      </span>
    </el-form>

    <div class="split">
      <div class="pane">
        <div class="pane-title">源码（Vue SFC，只能 <code>import 'vue'</code>）</div>
        <textarea
          v-model="form.source"
          class="code"
          spellcheck="false"
          wrap="off"
        ></textarea>
      </div>
      <div class="pane">
        <div class="pane-title">实时预览</div>
        <div class="preview-box">
          <div v-show="compileError" class="compile-error">{{ compileError }}</div>
          <div v-show="!compileError" ref="previewHost"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.viz-edit {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.topbar h1 {
  margin: 0;
  font-size: 1.3rem;
  font-weight: 600;
}
.actions {
  display: flex;
  gap: 10px;
}
.meta {
  margin-bottom: 8px;
}
.usage {
  color: var(--muted);
  font-size: 0.85rem;
  line-height: 32px;
}
.usage code,
.pane-title code {
  background: #f0f1f3;
  padding: 0.1em 0.4em;
  border-radius: 4px;
}
.split {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  background: #fff;
}
.pane-title {
  padding: 8px 12px;
  font-size: 0.82rem;
  color: var(--muted);
  border-bottom: 1px solid var(--border);
  background: #fafbfc;
}
.code {
  flex: 1;
  min-height: 320px;
  border: none;
  outline: none;
  resize: none;
  padding: 12px 14px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.6;
  tab-size: 2;
  white-space: pre;
}
.preview-box {
  flex: 1;
  overflow: auto;
  padding: 16px;
}
.compile-error {
  white-space: pre-wrap;
  color: #cf1322;
  background: #fff1f0;
  border: 1px solid #ffccc7;
  border-radius: 8px;
  padding: 12px 14px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.6;
}
</style>
