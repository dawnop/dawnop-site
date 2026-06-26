<script setup>
import { ref, onMounted, onBeforeUnmount, computed, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave, RouterLink } from 'vue-router'
import { Setting } from '@element-plus/icons-vue'
import { MdEditor } from 'md-editor-v3'
import '../../setupMdEditor'
import { pagesApi } from '../../api'
import { useEditorPreviewIslands } from '../../viz/editorPreview'
import { firstH1 } from '../../utils/markdownTitle'
import HelpTip from '../../components/HelpTip.vue'

// 内容页编辑器预览里把 ```viz 占位渲染成真实交互组件（类 mermaid）
const { onHtmlChanged } = useEditorPreviewIslands('page-editor-preview')

const route = useRoute()
const router = useRouter()

const id = computed(() => route.params.id)
const isEdit = computed(() => !!id.value)

const formRef = ref(null)
const form = ref({
  title: '',
  slug: '',
  type: 'content',
  description: '',
  content: '',
  auto_title: false, // 内容页：标题取正文第一个 # 一级标题
  nav_visible: true,
})

// 内容页开启「用正文 H1 作标题」时，标题跟随正文第一个一级标题
watch(
  [() => form.value.auto_title, () => form.value.content],
  ([auto, content]) => {
    if (auto && form.value.type === 'content') form.value.title = firstH1(content)
  }
)
const publishAt = ref(null) // Date | null，对应 created_at
const showSettings = ref(false)
const saving = ref(false)

// 文章列表页：展示该分类下的文章
const catArticles = ref([])
const catTotal = ref(0)
const catLoaded = ref(false)

const isList = computed(() => form.value.type === 'article_list')
const rules = {
  title: [
    {
      validator: (_rule, value, cb) => {
        if (!isList.value && form.value.auto_title) {
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
  return s ? `/p/${s}` : '/p/（留空将按标题自动生成）'
})

async function loadCategoryArticles(slug) {
  if (!slug) return
  try {
    const { data } = await pagesApi.listArticles(slug, 1, 50)
    catArticles.value = data.items
    catTotal.value = data.total
  } catch (e) {
    catArticles.value = []
    catTotal.value = 0
  } finally {
    catLoaded.value = true
  }
}

// ---- 未保存离开拦截 ----
let snapshot = ''
let justSaved = false
function serialize() {
  return JSON.stringify({
    ...form.value,
    publishAt: publishAt.value ? publishAt.value.getTime() : null,
  })
}
function takeSnapshot() {
  snapshot = serialize()
}
const dirty = computed(() => serialize() !== snapshot)

function beforeUnload(e) {
  if (dirty.value && !justSaved) {
    e.preventDefault()
    e.returnValue = ''
  }
}

onMounted(async () => {
  if (isEdit.value) {
    const { data } = await pagesApi.listAll()
    const p = data.find((x) => String(x.id) === String(id.value))
    if (!p) {
      ElMessage.error('页面不存在')
      router.replace('/admin/pages')
      return
    }
    form.value = {
      title: p.title,
      slug: p.slug,
      type: p.type,
      description: p.description || '',
      content: p.content || '',
      auto_title: p.auto_title,
      nav_visible: p.nav_visible,
    }
    publishAt.value = p.created_at ? new Date(p.created_at) : null
    if (p.type === 'article_list') loadCategoryArticles(p.slug)
  } else {
    // 新建：类型由列表页两个「新建」按钮决定（?type=content|article_list），不可再转换
    const t = route.query.type
    if (t === 'content' || t === 'article_list') form.value.type = t
    showSettings.value = true
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
      await pagesApi.update(id.value, payload)
    } else {
      await pagesApi.create(payload)
    }
    justSaved = true
    ElMessage.success('已保存')
    router.push('/admin/pages')
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
        {{ form.title || (form.auto_title ? '（正文暂无 # 标题）' : '未命名页面') }}
        <span v-if="form.auto_title && !isList" class="title-from">取自正文</span>
      </h1>
      <div class="actions">
        <el-tag type="info" effect="light">{{ isList ? '文章列表页' : '内容页' }}</el-tag>
        <el-button :icon="Setting" @click="showSettings = true">设置</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </div>
    </div>

    <!-- 内容页：Markdown 编辑器为核心 -->
    <MdEditor
      v-if="!isList"
      v-model="form.content"
      editor-id="page-editor"
      language="zh-CN"
      :preview="true"
      class="editor"
      @on-html-changed="onHtmlChanged"
    />

    <!-- 文章列表页：展示该分类下的文章 -->
    <el-card v-else shadow="never" class="list-panel">
      <div class="list-head">
        <span class="list-title">
          本栏目下的文章<span v-if="catLoaded" class="count">（{{ catTotal }} 篇）</span>
        </span>
        <RouterLink to="/admin/articles" class="manage-link">去文章管理 →</RouterLink>
      </div>
      <el-empty v-if="!isEdit" description="保存后即可在此查看分配到本栏目的文章" :image-size="80" />
      <el-empty
        v-else-if="catLoaded && catArticles.length === 0"
        description="还没有文章归属到本栏目。在文章设置里把「所属列表页」设为本页即可"
        :image-size="80"
      />
      <ul v-else class="cat-list">
        <li v-for="a in catArticles" :key="a.id">
          <RouterLink :to="`/admin/articles/${a.id}/edit`">{{ a.title }}</RouterLink>
          <el-tag :type="a.published ? 'success' : 'info'" size="small" effect="light">
            {{ a.published ? '已发布' : '草稿' }}
          </el-tag>
        </li>
      </ul>
    </el-card>

    <!-- 右侧设置抽屉 -->
    <el-drawer v-model="showSettings" title="页面设置" size="360px">
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
        <el-form-item label="标题" prop="title">
          <el-input
            v-model="form.title"
            :disabled="!isList && form.auto_title"
            :placeholder="!isList && form.auto_title ? '取自正文第一个 # 标题' : '页面标题'"
          />
          <el-checkbox v-if="!isList" v-model="form.auto_title" size="small" class="auto-title-chk">
            用正文第一个 # 标题作为标题
            <HelpTip>
              开启后标题自动取正文里第一个一级标题（<code># 标题</code>），
              页面渲染时会隐藏正文那行以免重复；正文按原文完整保存。
            </HelpTip>
          </el-checkbox>
        </el-form-item>
        <el-form-item label="类型">
          <el-input :value="isList ? '文章列表页（充当分类）' : '内容页（Markdown）'" disabled />
          <div class="field-hint">类型在创建时确定，不可转换。</div>
        </el-form-item>
        <el-form-item>
          <template #label>
            路径 slug
            <HelpTip>访问地址 <code>{{ slugPreview }}</code></HelpTip>
          </template>
          <el-input v-model="form.slug" placeholder="url-friendly-slug" />
        </el-form-item>
        <el-form-item label="页面描述（SEO / 列表说明，可选）">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="一句话描述这个页面" />
        </el-form-item>
        <el-form-item label="发布时间">
          <el-date-picker
            v-model="publishAt"
            type="datetime"
            placeholder="留空则用当前时间"
            class="w-full"
          />
        </el-form-item>
        <el-form-item>
          <el-switch v-model="form.nav_visible" active-text="显示在导航栏" />
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
.list-panel .list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.list-title {
  font-size: 1.05rem;
  font-weight: 600;
}
.count {
  color: var(--muted);
  font-weight: 400;
  font-size: 0.9rem;
}
.manage-link {
  color: var(--accent);
  text-decoration: none;
  font-size: 0.9rem;
}
.cat-list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
}
.cat-list li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 0;
  border-bottom: 1px solid var(--border);
}
.cat-list li:last-child {
  border-bottom: none;
}
.cat-list a {
  color: var(--fg);
  text-decoration: none;
}
.cat-list a:hover {
  color: var(--accent);
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
