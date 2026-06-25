<script setup>
import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { Setting, MagicStick, ArrowDown } from '@element-plus/icons-vue'
import { MdEditor } from 'md-editor-v3'
import '../../setupMdEditor'
import { articlesApi, pagesApi, vizApi } from '../../api'
import { builtinIds } from '../../viz/registry'
import { useEditorPreviewIslands } from '../../viz/editorPreview'

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
  page_id: null,
})

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
  title: [{ required: true, message: '请输入标题', trigger: 'blur' }],
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
      page_id: data.page_id,
    }
    publishAt.value = data.created_at ? new Date(data.created_at) : null
  } else {
    showSettings.value = true // 新建默认展开设置抽屉
  }
  takeSnapshot()
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
      <h1 class="doc-title">{{ form.title || '未命名文章' }}</h1>
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
        <el-form-item label="标题" prop="title">
          <el-input v-model="form.title" placeholder="文章标题" />
        </el-form-item>
        <el-form-item label="Slug">
          <el-input v-model="form.slug" placeholder="url-friendly-slug" />
          <div class="field-hint">访问地址：<code>{{ slugPreview }}</code></div>
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
