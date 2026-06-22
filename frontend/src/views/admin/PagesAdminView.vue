<script setup>
import { ref, onMounted } from 'vue'
import { MdEditor } from 'md-editor-v3'
import { pagesApi } from '../../api'

const pages = ref([])
const loading = ref(true)
const error = ref('')

// 编辑中的页面（null=未打开；无 id=新建）
const editing = ref(null)
const saving = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await pagesApi.listAll()
    pages.value = data
  } catch (e) {
    error.value = '加载失败，请确认后端已启动并已登录。'
  } finally {
    loading.value = false
  }
}

function newPage() {
  editing.value = {
    title: '',
    slug: '',
    type: 'article_list',
    content: '',
    nav_visible: true,
  }
}

function edit(p) {
  editing.value = { ...p }
}

async function save() {
  if (!editing.value.title.trim()) return
  saving.value = true
  try {
    const payload = {
      title: editing.value.title,
      slug: editing.value.slug,
      type: editing.value.type,
      content: editing.value.content,
      nav_visible: editing.value.nav_visible,
    }
    if (editing.value.id) {
      await pagesApi.update(editing.value.id, payload)
    } else {
      await pagesApi.create(payload)
    }
    editing.value = null
    load()
  } finally {
    saving.value = false
  }
}

async function remove(p) {
  if (!confirm(`确定删除页面「${p.title}」？其下文章会解除归属。`)) return
  await pagesApi.remove(p.id)
  load()
}

async function move(index, delta) {
  const target = index + delta
  if (target < 0 || target >= pages.value.length) return
  const ids = pages.value.map((p) => p.id)
  ;[ids[index], ids[target]] = [ids[target], ids[index]]
  const { data } = await pagesApi.reorder(ids)
  pages.value = data
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <h1>页面管理</h1>
      <button class="primary" @click="newPage">新建页面</button>
    </div>
    <p class="hint">导航顺序即下表顺序；首页固定在最前，不在此列。</p>

    <div class="card">
      <p v-if="error" class="error">{{ error }}</p>
      <p v-else-if="loading" class="muted">加载中…</p>
      <table v-else class="data-grid">
        <thead>
          <tr>
            <th>标题</th>
            <th>类型</th>
            <th>路径</th>
            <th>导航</th>
            <th class="ops">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(p, i) in pages" :key="p.id">
            <td>{{ p.title }}</td>
            <td class="muted">{{ p.type === 'content' ? '内容页' : '文章列表' }}</td>
            <td class="muted">/p/{{ p.slug }}</td>
            <td>
              <span :class="['badge', p.nav_visible ? 'on' : 'off']">
                {{ p.nav_visible ? '显示' : '隐藏' }}
              </span>
            </td>
            <td class="ops">
              <a @click.prevent="move(i, -1)">↑</a>
              <a @click.prevent="move(i, 1)">↓</a>
              <a @click.prevent="edit(p)">编辑</a>
              <a class="danger" @click.prevent="remove(p)">删除</a>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 编辑面板 -->
    <div v-if="editing" class="card editor">
      <div class="editor-head">
        <h2>{{ editing.id ? '编辑页面' : '新建页面' }}</h2>
        <div class="actions">
          <button @click="editing = null">取消</button>
          <button class="primary" :disabled="saving" @click="save">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>

      <label>标题</label>
      <input v-model="editing.title" placeholder="页面标题" />

      <label>路径 slug（留空自动生成）</label>
      <input v-model="editing.slug" placeholder="url-friendly-slug" />

      <label>类型</label>
      <select v-model="editing.type">
        <option value="article_list">文章列表页（充当分类）</option>
        <option value="content">内容页（Markdown）</option>
      </select>

      <template v-if="editing.type === 'content'">
        <label>正文（Markdown，支持 $LaTeX$）</label>
        <MdEditor
          v-model="editing.content"
          language="zh-CN"
          :preview="true"
          class="page-editor"
        />
      </template>

      <label class="checkbox">
        <input type="checkbox" v-model="editing.nav_visible" />
        显示在导航栏
      </label>
    </div>
  </div>
</template>

<style scoped>
.hint {
  margin: -8px 0 16px;
}
.editor {
  margin-top: 20px;
}
.editor-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.editor-head h2 {
  margin: 0;
  font-size: 1.1rem;
}
.editor-head .actions {
  display: flex;
  gap: 10px;
}
.page-editor {
  height: 460px;
  border-radius: var(--radius);
  overflow: hidden;
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
</style>
