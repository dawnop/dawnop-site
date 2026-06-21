<script setup>
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { articlesApi } from '../api'
import MarkdownView from '../components/MarkdownView.vue'

const route = useRoute()
const article = ref(null)
const loading = ref(true)
const notFound = ref(false)

async function load(slug) {
  loading.value = true
  notFound.value = false
  article.value = null
  try {
    const { data } = await articlesApi.getBySlug(slug)
    article.value = data
  } catch (e) {
    notFound.value = true
  } finally {
    loading.value = false
  }
}

function fmtDate(s) {
  return new Date(s).toLocaleDateString('zh-CN')
}

watch(() => route.params.slug, (slug) => slug && load(slug), { immediate: true })
</script>

<template>
  <article v-if="article">
    <h1 class="title">{{ article.title }}</h1>
    <div class="meta">{{ fmtDate(article.created_at) }}</div>
    <MarkdownView :source="article.content" />
  </article>
  <p v-else-if="loading" class="muted">加载中…</p>
  <p v-else-if="notFound" class="muted">文章不存在或未发布。</p>
</template>

<style scoped>
.title {
  font-size: 2rem;
  margin: 0 0 6px;
}
.meta {
  color: #8b949e;
  font-size: 0.85rem;
  margin-bottom: 24px;
}
.muted {
  color: #8b949e;
}
</style>
