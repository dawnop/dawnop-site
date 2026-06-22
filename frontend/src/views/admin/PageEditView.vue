<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { MdEditor } from 'md-editor-v3'
import { pagesApi } from '../../api'

const route = useRoute()
const router = useRouter()

const id = computed(() => route.params.id)
const isEdit = computed(() => !!id.value)

const form = ref({
  title: '',
  slug: '',
  type: 'content',
  description: '',
  content: '',
  nav_visible: true,
})
const showSettings = ref(true)
const saving = ref(false)
const error = ref('')

// 文章列表页：展示该分类下的文章
const catArticles = ref([])
const catTotal = ref(0)
const catLoaded = ref(false)
const originalType = ref(null) // 进入编辑时的类型，用于切换类型的安全提示

const isList = computed(() => form.value.type === 'article_list')
// slug 实时预览：留空时后端按标题自动生成
const slugPreview = computed(() => {
  const s = form.value.slug.trim()
  return s ? `/p/${s}` : '/p/（留空将根据标题自动生成）'
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

onMounted(async () => {
  if (isEdit.value) {
    const { data } = await pagesApi.listAll()
    const p = data.find((x) => String(x.id) === String(id.value))
    if (!p) {
      error.value = '页面不存在'
      return
    }
    form.value = {
      title: p.title,
      slug: p.slug,
      type: p.type,
      description: p.description || '',
      content: p.content || '',
      nav_visible: p.nav_visible,
    }
    originalType.value = p.type
    if (p.type === 'article_list') loadCategoryArticles(p.slug)
  }
})

async function save() {
  if (!form.value.title.trim()) {
    error.value = '标题不能为空'
    showSettings.value = true
    return
  }
  // 安全提示：从「文章列表页」改成「内容页」且栏目下有文章时，提醒前台将不再展示（数据保留、可逆）
  if (
    isEdit.value &&
    originalType.value === 'article_list' &&
    form.value.type === 'content' &&
    catTotal.value > 0
  ) {
    const ok = confirm(
      `本栏目下有 ${catTotal.value} 篇文章，切换为内容页后前台将不再展示它们` +
        `（数据保留，切回「文章列表页」即可恢复）。确定切换？`
    )
    if (!ok) return
  }
  saving.value = true
  error.value = ''
  try {
    if (isEdit.value) {
      await pagesApi.update(id.value, form.value)
    } else {
      await pagesApi.create(form.value)
    }
    router.push('/admin/pages')
  } catch (e) {
    error.value = '保存失败，请重试'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="edit-wrap">
    <div class="page-head">
      <div class="title-row">
        <RouterLink to="/admin/pages" class="back" title="返回列表">←</RouterLink>
        <input class="title-input" v-model="form.title" placeholder="页面标题" />
      </div>
      <div class="actions">
        <button @click="showSettings = !showSettings">
          {{ showSettings ? '收起设置' : '页面设置' }}
        </button>
        <button class="primary" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </div>
    </div>

    <p v-if="error" class="error">{{ error }}</p>

    <!-- 可折叠：页面设置 -->
    <div v-show="showSettings" class="card settings">
      <div class="grid2">
        <div>
          <label>类型</label>
          <select v-model="form.type">
            <option value="content">内容页（Markdown）</option>
            <option value="article_list">文章列表页（充当分类）</option>
          </select>
        </div>
        <div>
          <label>路径 slug</label>
          <input v-model="form.slug" placeholder="url-friendly-slug" />
          <p class="field-hint">访问地址：<code>{{ slugPreview }}</code></p>
        </div>
      </div>
      <label>页面摘要 / 描述（可选，用于 SEO 与列表说明）</label>
      <input v-model="form.description" placeholder="一句话描述这个页面" />
      <label class="checkbox">
        <input type="checkbox" v-model="form.nav_visible" />
        显示在导航栏
      </label>
    </div>

    <!-- 内容页：Markdown 编辑器为核心 -->
    <MdEditor
      v-if="!isList"
      v-model="form.content"
      language="zh-CN"
      :preview="true"
      class="editor"
    />

    <!-- 文章列表页：展示该分类下的文章 -->
    <div v-else class="card list-panel">
      <div class="list-head">
        <h2>本栏目下的文章<span v-if="catLoaded" class="count">（{{ catTotal }} 篇）</span></h2>
        <RouterLink to="/admin/articles" class="manage-link">去文章管理 →</RouterLink>
      </div>
      <p v-if="!isEdit" class="muted">保存后即可在此查看分配到本栏目的文章。</p>
      <p v-else-if="catLoaded && catArticles.length === 0" class="muted">
        还没有文章归属到本栏目。在文章设置里把文章的「所属列表页」设为本页即可。
      </p>
      <ul v-else class="cat-list">
        <li v-for="a in catArticles" :key="a.id">
          <RouterLink :to="`/admin/articles/${a.id}/edit`">{{ a.title }}</RouterLink>
          <span :class="['badge', a.published ? 'pub' : 'draft']">
            {{ a.published ? '已发布' : '草稿' }}
          </span>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.edit-wrap {
  display: flex;
  flex-direction: column;
}
.title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  min-width: 0;
}
.back {
  text-decoration: none;
  font-size: 1.3rem;
  color: var(--muted);
  line-height: 1;
}
.back:hover {
  color: var(--accent);
}
.title-input {
  font-size: 1.25rem;
  font-weight: 600;
  border: none;
  padding: 4px 2px;
  border-bottom: 1px solid transparent;
  border-radius: 0;
  width: 100%;
  max-width: 560px;
}
.title-input:focus {
  outline: none;
  border-bottom-color: var(--accent);
  box-shadow: none;
}
.settings {
  margin-bottom: 14px;
}
.settings .grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.settings label:first-child,
.settings .grid2 label {
  margin-top: 0;
}
.field-hint {
  margin: 6px 0 0;
  font-size: 0.82rem;
  color: var(--muted);
}
.field-hint code {
  background: #f0f1f3;
  padding: 0.1em 0.4em;
  border-radius: 4px;
}
.checkbox {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--fg);
}
.checkbox input {
  width: auto;
}
.editor {
  height: calc(100vh - 240px);
  min-height: 420px;
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
.list-panel h2 {
  margin: 0;
  font-size: 1.05rem;
}
.list-panel .count {
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
.muted {
  color: var(--muted);
}
</style>
