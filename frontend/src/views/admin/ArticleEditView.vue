<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { MdEditor } from 'md-editor-v3'
import { articlesApi, pagesApi } from '../../api'

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
const listPages = ref([])
const showSettings = ref(false)
const saving = ref(false)
const error = ref('')

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
    if (isEdit.value) {
      await articlesApi.update(id.value, form.value)
    } else {
      await articlesApi.create(form.value)
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
    <div class="page-head">
      <div class="title-row">
        <RouterLink to="/admin/articles" class="back" title="返回列表">←</RouterLink>
        <input class="title-input" v-model="form.title" placeholder="文章标题" />
      </div>
      <div class="actions">
        <button @click="showSettings = !showSettings">
          {{ showSettings ? '收起设置' : '文章设置' }}
        </button>
        <button class="primary" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </div>
    </div>

    <p v-if="error" class="error">{{ error }}</p>

    <!-- 可折叠：文章设置 -->
    <div v-show="showSettings" class="card settings">
      <div class="grid2">
        <div>
          <label>Slug（留空自动生成）</label>
          <input v-model="form.slug" placeholder="url-friendly-slug" />
        </div>
        <div>
          <label>所属列表页（分类，可不选）</label>
          <select v-model="form.page_id">
            <option :value="null">— 不分配 —</option>
            <option v-for="p in listPages" :key="p.id" :value="p.id">{{ p.title }}</option>
          </select>
        </div>
      </div>
      <label>摘要</label>
      <input v-model="form.summary" placeholder="一句话摘要（可选）" />
      <label class="checkbox">
        <input type="checkbox" v-model="form.published" />
        立即发布
      </label>
    </div>

    <!-- 核心：Markdown 编辑器 -->
    <MdEditor
      v-model="form.content"
      language="zh-CN"
      :preview="true"
      class="editor"
    />
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
.checkbox {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--fg);
}
.checkbox input {
  width: auto;
}
/* 圆角 + 阴影，与后台卡片观感一致（md-editor 默认直角） */
.editor {
  height: calc(100vh - 210px);
  min-height: 440px;
  border-radius: var(--radius);
  overflow: hidden;
  box-shadow: var(--shadow-card);
}
</style>
