<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { articlesApi } from '../../api'
import MarkdownView from '../../components/MarkdownView.vue'

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
})
const showPreview = ref(false)
const saving = ref(false)
const error = ref('')

onMounted(async () => {
  if (isEdit.value) {
    const { data } = await articlesApi.getForEdit(id.value)
    form.value = {
      title: data.title,
      slug: data.slug,
      summary: data.summary,
      content: data.content,
      published: data.published,
    }
  }
})

async function save() {
  if (!form.value.title.trim()) {
    error.value = '标题不能为空'
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
  <div>
    <div class="toolbar">
      <h1>{{ isEdit ? '编辑文章' : '写文章' }}</h1>
      <div class="actions">
        <button @click="showPreview = !showPreview">
          {{ showPreview ? '编辑' : '预览' }}
        </button>
        <button class="primary" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </div>
    </div>

    <label>标题</label>
    <input v-model="form.title" placeholder="文章标题" />

    <label>Slug（留空自动生成）</label>
    <input v-model="form.slug" placeholder="url-friendly-slug" />

    <label>摘要</label>
    <input v-model="form.summary" placeholder="一句话摘要（可选）" />

    <label>正文（Markdown，支持 $LaTeX$）</label>
    <MarkdownView v-if="showPreview" :source="form.content" class="preview-box" />
    <textarea v-else v-model="form.content" rows="20" class="editor"></textarea>

    <label class="checkbox">
      <input type="checkbox" v-model="form.published" />
      立即发布
    </label>

    <p v-if="error" class="error">{{ error }}</p>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.actions {
  display: flex;
  gap: 10px;
}
.editor {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.9rem;
  line-height: 1.6;
  resize: vertical;
}
.preview-box {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 16px;
  min-height: 200px;
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
.error {
  color: #b91c1c;
  margin-top: 12px;
}
</style>
