<script setup>
import { ref, watch, onMounted, onBeforeUnmount, computed, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { articlesApi } from '../api'
import MarkdownView from '../components/MarkdownView.vue'
import { stripFirstH1 } from '../utils/markdownTitle'

const route = useRoute()
const article = ref(null)
const loading = ref(true)
const notFound = ref(false)

const headings = ref([])
const activeId = ref('')

let hashHandled = false

async function load(slug) {
  loading.value = true
  notFound.value = false
  article.value = null
  headings.value = []
  hashHandled = false
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

const readMinutes = computed(() =>
  article.value ? Math.max(1, Math.round((article.value.word_count || 0) / 300)) : 0
)

// 标题取自正文 H1 时，渲染前剥离那行，避免与上方标题重复（存储仍是完整原文）
const displayContent = computed(() => {
  if (!article.value) return ''
  return article.value.auto_title
    ? stripFirstH1(article.value.content)
    : article.value.content
})

// 目录：取 h1~h3
const toc = computed(() => headings.value.filter((h) => h.level <= 3))

function onHeadings(h) {
  headings.value = h
  nextTick(() => {
    updateActive()
    // 若是带 #标题 的分享链接首次进入，渲染完成后滚到对应标题
    if (!hashHandled && location.hash) {
      const id = decodeURIComponent(location.hash.slice(1))
      const el = document.getElementById(id)
      if (el) {
        hashHandled = true
        el.scrollIntoView()
      }
    }
  })
}

// 滚动高亮当前章节
let raf = 0
function updateActive() {
  let cur = toc.value[0]?.id || ''
  for (const h of toc.value) {
    const el = document.getElementById(h.id)
    if (el && el.getBoundingClientRect().top <= 100) cur = h.id
    else break
  }
  activeId.value = cur
}
function onScroll() {
  if (raf) return
  raf = requestAnimationFrame(() => {
    raf = 0
    updateActive()
  })
}
function goTo(id) {
  // 仅平滑滚动，不改 location.hash（避免与 history 路由相互干扰）
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
}

onMounted(() => window.addEventListener('scroll', onScroll, { passive: true }))
onBeforeUnmount(() => window.removeEventListener('scroll', onScroll))

watch(() => route.params.slug, (slug) => slug && load(slug), { immediate: true })
</script>

<template>
  <div class="article-wrap">
    <article v-if="article" class="article">
      <header class="head">
        <h1 class="title">{{ article.title }}</h1>
        <div class="meta">
          <span>{{ fmtDate(article.created_at) }}</span>
          <span class="dot">·</span>
          <span>{{ article.word_count }} 字</span>
          <span class="dot">·</span>
          <span>约 {{ readMinutes }} 分钟</span>
        </div>
      </header>
      <MarkdownView :source="displayContent" @headings="onHeadings" />
    </article>

    <aside v-if="article && toc.length > 1" class="toc">
      <div class="toc-title">目录</div>
      <ul>
        <li
          v-for="h in toc"
          :key="h.id"
          :class="['lv' + h.level, { active: activeId === h.id }]"
        >
          <a :href="'#' + h.id" @click.prevent="goTo(h.id)">{{ h.text }}</a>
        </li>
      </ul>
    </aside>

    <el-skeleton v-if="loading" :rows="8" animated />
    <el-result
      v-else-if="notFound"
      icon="warning"
      title="文章不存在或未发布"
      sub-title="该文章可能已被删除、转为草稿或地址有误"
    >
      <template #extra>
        <el-button type="primary" @click="$router.push('/')">返回首页</el-button>
      </template>
    </el-result>
  </div>
</template>

<style scoped>
.article-wrap {
  position: relative;
}
.article {
  max-width: 720px;
  margin: 0 auto;
}
.head {
  margin-bottom: 28px;
}
.title {
  font-size: 2.4rem;
  line-height: 1.25;
  letter-spacing: -0.01em;
  margin: 0 0 14px;
}
.meta {
  color: #8b949e;
  font-size: 0.88rem;
}
.meta .dot {
  margin: 0 8px;
  color: #c9cdd4;
}

/* 目录：宽屏固定在右侧留白区，窄屏隐藏 */
.toc {
  display: none;
}
@media (min-width: 1200px) {
  .toc {
    display: block;
    position: fixed;
    top: 96px;
    left: calc(50% + 380px);
    width: 200px;
    max-height: calc(100vh - 140px);
    overflow-y: auto;
    font-size: 0.85rem;
  }
}
.toc-title {
  font-weight: 600;
  color: var(--fg);
  margin-bottom: 10px;
}
.toc ul {
  list-style: none;
  margin: 0;
  padding: 0;
  border-left: 2px solid var(--border);
}
.toc li {
  margin: 0;
}
.toc li a {
  display: block;
  padding: 5px 0 5px 14px;
  margin-left: -2px;
  border-left: 2px solid transparent;
  color: var(--muted);
  text-decoration: none;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.toc li.lv3 a {
  padding-left: 28px;
  font-size: 0.95em;
}
.toc li a:hover {
  color: var(--fg);
}
.toc li.active a {
  color: var(--accent);
  border-left-color: var(--accent);
}
.muted {
  color: #8b949e;
}
</style>
