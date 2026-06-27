<script setup>
import { ref, onMounted, onBeforeUnmount, computed, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { Setting, MagicStick, ArrowDown } from '@element-plus/icons-vue'
import { MdEditor } from 'md-editor-v3'
import '../../setupMdEditor'
import { articlesApi, pagesApi, vizApi } from '../../api'
import { builtinIds } from '../../viz/registry'
import { useEditorPreviewIslands } from '../../viz/editorPreview'
import { firstH1 } from '../../utils/markdownTitle'
import HelpTip from '../../components/HelpTip.vue'

const IMPORT_KEY = 'dawnop_import_md' // 导入 .md 时由列表页写入、本页读取后清除
const META_KEY = 'dawnop_import_meta' // 导入 .md 的 front matter 元数据（同上）
const NAME_KEY = 'dawnop_import_name' // 导入 .md 的文件名（无 title/H1 时兜底当标题）

// 编辑器预览里把 ```viz 占位渲染成真实交互组件（类 mermaid）
const { onHtmlChanged } = useEditorPreviewIslands('article-editor-preview')

const route = useRoute()
const router = useRouter()

const id = computed(() => route.params.id)
const isEdit = computed(() => !!id.value)

const formRef = ref(null)
const editorRef = ref(null)
const vizOptions = ref([]) // 可插入的可视化标识（内置 + 后台动态）
const form = ref({
  title: '',
  slug: '',
  summary: '',
  content: '',
  published: false,
  auto_title: false, // 开启后标题取正文第一个 # 一级标题
  page_id: null,
})

// 开启「用正文 H1 作标题」时，标题跟随正文第一个一级标题
watch(
  [() => form.value.auto_title, () => form.value.content],
  ([auto, content]) => {
    if (auto) form.value.title = firstH1(content)
  }
)

// 在光标处插入 ```viz <slug>``` 围栏
function insertViz(slug) {
  editorRef.value?.insert(() => ({
    targetValue: `\n\`\`\`viz\n${slug}\n\`\`\`\n`,
    select: false,
    deviationStart: 0,
    deviationEnd: 0,
  }))
}
const publishAt = ref(null) // Date | null，对应 created_at（发布时间）
const listPages = ref([])
const showSettings = ref(false)
const saving = ref(false)

const rules = {
  title: [
    {
      validator: (_rule, value, cb) => {
        if (form.value.auto_title) {
          if (!firstH1(form.value.content)) {
            cb(new Error('已开启「用正文标题」，但正文里没有 # 一级标题'))
          } else cb()
        } else if (!value || !value.trim()) {
          cb(new Error('请输入标题'))
        } else cb()
      },
      trigger: 'blur',
    },
  ],
}

const slugPreview = computed(() => {
  const s = form.value.slug.trim()
  return s ? `/article/${s}` : '/article/（留空将按标题自动生成）'
})

// ---- 未保存离开拦截：用快照对比判断脏 ----
let snapshot = ''
let justSaved = false
function takeSnapshot() {
  snapshot = serialize()
}
function serialize() {
  return JSON.stringify({
    ...form.value,
    publishAt: publishAt.value ? publishAt.value.getTime() : null,
  })
}
const dirty = computed(() => serialize() !== snapshot)

function beforeUnload(e) {
  if (dirty.value && !justSaved) {
    e.preventDefault()
    e.returnValue = ''
  }
}

onMounted(async () => {
  const { data: allPages } = await pagesApi.listAll()
  listPages.value = allPages.filter((p) => p.type === 'article_list')

  // 可插入的可视化：内置 + 后台动态组件（去重）
  try {
    const { data: vizList } = await vizApi.listAll()
    vizOptions.value = [...new Set([...builtinIds(), ...vizList.map((v) => v.slug)])]
  } catch (e) {
    vizOptions.value = builtinIds()
  }

  if (isEdit.value) {
    const { data } = await articlesApi.getForEdit(id.value)
    form.value = {
      title: data.title,
      slug: data.slug,
      summary: data.summary,
      content: data.content,
      published: data.published,
      auto_title: data.auto_title,
      page_id: data.page_id,
    }
    publishAt.value = data.created_at ? new Date(data.created_at) : null
    takeSnapshot()
  } else {
    showSettings.value = true // 新建默认展开设置抽屉
    // 「导入 .md」：列表页已解析 front matter，正文与元数据分别存入 sessionStorage，这里读出预填
    const imported = sessionStorage.getItem(IMPORT_KEY)
    if (imported !== null) {
      sessionStorage.removeItem(IMPORT_KEY)
      const metaRaw = sessionStorage.getItem(META_KEY)
      sessionStorage.removeItem(META_KEY)
      const fileName = sessionStorage.getItem(NAME_KEY)
      sessionStorage.removeItem(NAME_KEY)
      const meta = metaRaw ? JSON.parse(metaRaw) : {}
      form.value.content = imported
      // 标题优先级：front matter title → 文件名（去 .md）；导入一律不开启 auto_title。
      // H1 逻辑仅在用户手动勾选「用正文标题」开关时生效（见顶部 watch）。
      const fname = (fileName || '').replace(/\.(md|markdown)$/i, '').trim()
      form.value.title = (meta.title ? String(meta.title) : '') || fname
      form.value.auto_title = false
      if (meta.summary != null) form.value.summary = String(meta.summary)
      if (meta.slug != null) form.value.slug = String(meta.slug)
      if (typeof meta.published === 'boolean') form.value.published = meta.published
      // 不对导入内容建快照 → 呈「未保存」，提醒保存后再离开
    } else {
      takeSnapshot()
    }
  }
  window.addEventListener('beforeunload', beforeUnload)
})

onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))

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
    showSettings.value = true
    ElMessage.warning('请完善必填项')
    return
  }
  saving.value = true
  try {
    const payload = { ...form.value }
    if (publishAt.value) payload.created_at = new Date(publishAt.value).toISOString()
    if (isEdit.value) {
      await articlesApi.update(id.value, payload)
    } else {
      await articlesApi.create(payload)
    }
    justSaved = true
    ElMessage.success('已保存')
    router.push('/admin/articles')
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="edit-wrap">
    <div class="edit-topbar">
      <h1 class="doc-title">
        {{ form.title || (form.auto_title ? '（正文暂无 # 标题）' : '未命名文章') }}
        <span v-if="form.auto_title" class="title-from">取自正文</span>
      </h1>
      <div class="actions">
        <el-tag :type="form.published ? 'success' : 'info'" effect="light">
          {{ form.published ? '已发布' : '草稿' }}
        </el-tag>
        <el-dropdown trigger="click" @command="insertViz">
          <el-button :icon="MagicStick">
            插入可视化<el-icon class="el-icon--right"><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-if="!vizOptions.length" disabled>暂无可视化</el-dropdown-item>
              <el-dropdown-item v-for="s in vizOptions" :key="s" :command="s">{{ s }}</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button :icon="Setting" @click="showSettings = true">设置</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </div>
    </div>

    <MdEditor
      ref="editorRef"
      v-model="form.content"
      editor-id="article-editor"
      language="zh-CN"
      :preview="true"
      class="editor"
      @on-html-changed="onHtmlChanged"
    />

    <!-- 右侧设置抽屉 -->
    <el-drawer v-model="showSettings" title="文章设置" size="360px">
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
        <el-form-item prop="title">
          <template #label>
            标题
            <HelpTip>
              导入 .md 时，标题按优先级确定（默认不开启下方开关）：<br />
              ① front matter 的 <code>title:</code>；<br />
              ② 否则用文件名（去掉 <code>.md</code>）。<br />
              勾选下方开关后，标题改取正文第一个 <code># 标题</code> 并跟随它。
            </HelpTip>
          </template>
          <el-input
            v-model="form.title"
            :disabled="form.auto_title"
            :placeholder="form.auto_title ? '取自正文第一个 # 标题' : '文章标题'"
          />
          <el-checkbox v-model="form.auto_title" size="small" class="auto-title-chk">
            用正文第一个 # 标题作为标题
            <HelpTip>
              开启后标题自动取正文里第一个一级标题（<code># 标题</code>），
              文章页渲染时会隐藏正文那行以免重复；正文按原文完整保存。
            </HelpTip>
          </el-checkbox>
        </el-form-item>
        <el-form-item>
          <template #label>
            Slug
            <HelpTip>访问地址 <code>{{ slugPreview }}</code></HelpTip>
          </template>
          <el-input v-model="form.slug" placeholder="url-friendly-slug" />
        </el-form-item>
        <el-form-item label="所属列表页（分类）">
          <el-select v-model="form.page_id" placeholder="未分类" clearable class="w-full">
            <el-option v-for="p in listPages" :key="p.id" :label="p.title" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="摘要">
          <el-input v-model="form.summary" type="textarea" :rows="2" placeholder="一句话摘要（可选）" />
        </el-form-item>
        <el-form-item label="发布时间">
          <el-date-picker
            v-model="publishAt"
            type="datetime"
            placeholder="留空则用当前时间"
            class="w-full"
          />
          <div class="field-hint">前台显示与排序用此时间。</div>
        </el-form-item>
        <el-form-item>
          <el-switch v-model="form.published" active-text="立即发布" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="primary" :loading="saving" class="w-full" @click="save">保存</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.edit-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.edit-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}
.doc-title {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: 1.3rem;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.title-from {
  margin-left: 8px;
  font-size: 0.7rem;
  font-weight: 400;
  color: var(--muted);
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  padding: 1px 6px;
  vertical-align: middle;
}
.auto-title-chk {
  margin-top: 6px;
}
.actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.editor {
  flex: 1;
  min-height: 360px;
  border-radius: var(--radius);
  overflow: hidden;
  box-shadow: var(--shadow-card);
}
.field-hint {
  margin-top: 6px;
  font-size: 0.82rem;
  color: var(--muted);
  line-height: 1.5;
}
.field-hint code {
  background: #f0f1f3;
  padding: 0.1em 0.4em;
  border-radius: 4px;
}
.w-full {
  width: 100%;
}
</style>
