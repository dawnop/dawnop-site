<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { MdEditor } from 'md-editor-v3'
import { articlesApi, pagesApi } from '../../api'
import SettingsDrawer from '../../components/SettingsDrawer.vue'

const route = useRoute()
const router = useRouter()

const id = computed(() => route.params.id)
const isEdit = computed(() => !!id.value)

const form = ref({
  title: '',
  slug: '',
  summary: '',
  content: '',
  published: false,
  page_id: null,
})
const publishAt = ref('') // datetime-local 字符串；对应 created_at（发布时间）
const listPages = ref([])
const showSettings = ref(false)
const saving = ref(false)
const error = ref('')

// ISO/带时区时间 → datetime-local 输入值（本地时区，YYYY-MM-DDTHH:mm）
function toLocalInput(v) {
  if (!v) return ''
  const d = new Date(v)
  if (isNaN(d)) return ''
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

const slugPreview = computed(() => {
  const s = form.value.slug.trim()
  return s ? `/article/${s}` : '/article/（留空将根据标题自动生成）'
})

onMounted(async () => {
  const { data: allPages } = await pagesApi.listAll()
  listPages.value = allPages.filter((p) => p.type === 'article_list')

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
    publishAt.value = toLocalInput(data.created_at)
  } else {
    showSettings.value = true // 新建默认展开设置抽屉（标题等在抽屉内）
  }
})

async function save() {
  if (!form.value.title.trim()) {
    error.value = '标题不能为空'
    showSettings.value = true
    return
  }
  saving.value = true
  error.value = ''
  try {
    const payload = { ...form.value }
    if (publishAt.value) payload.created_at = new Date(publishAt.value).toISOString()
    if (isEdit.value) {
      await articlesApi.update(id.value, payload)
    } else {
      await articlesApi.create(payload)
    }
    router.push('/admin/articles')
  } catch (e) {
    error.value = '保存失败'
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
        <span v-if="form.published" class="badge pub">已发布</span>
        <span v-else class="badge draft">草稿</span>
        <button @click="showSettings = true">设置</button>
        <button class="primary" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </div>
    </div>

    <p v-if="error" class="error">{{ error }}</p>

    <!-- 核心：Markdown 编辑器（满区，设置抽屉覆盖右侧、不挤压） -->
    <MdEditor v-model="form.content" language="zh-CN" :preview="true" class="editor" />

    <!-- 右侧设置抽屉 -->
    <SettingsDrawer v-model="showSettings" title="文章设置">
      <label>标题</label>
      <input v-model="form.title" placeholder="文章标题" />

      <label>Slug</label>
      <input v-model="form.slug" placeholder="url-friendly-slug" />
      <p class="field-hint">访问地址：<code>{{ slugPreview }}</code></p>

      <label>所属列表页（分类，可不选）</label>
      <select v-model="form.page_id">
        <option :value="null">— 不分配 —</option>
        <option v-for="p in listPages" :key="p.id" :value="p.id">{{ p.title }}</option>
      </select>

      <label>摘要</label>
      <input v-model="form.summary" placeholder="一句话摘要（可选）" />

      <label>发布时间</label>
      <input type="datetime-local" v-model="publishAt" />
      <p class="field-hint">前台显示与排序用此时间；留空则用当前时间。</p>

      <label class="checkbox">
        <input type="checkbox" v-model="form.published" />
        立即发布
      </label>

      <template #footer>
        <button class="primary block" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </template>
    </SettingsDrawer>
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
.block {
  width: 100%;
}
.editor {
  flex: 1;
  min-height: 360px;
  border-radius: var(--radius);
  overflow: hidden;
  box-shadow: var(--shadow-card);
}
</style>
