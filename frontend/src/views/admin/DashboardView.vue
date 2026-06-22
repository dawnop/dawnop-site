<script setup>
import { ref, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { articlesApi, pagesApi } from '../../api'

const stats = ref({ articles: null, drafts: null, pages: null })

onMounted(async () => {
  try {
    const [all, draft, pages] = await Promise.all([
      articlesApi.listAll(1, 1),
      articlesApi.listAll(1, 1, { published: false }),
      pagesApi.listAll(),
    ])
    stats.value = {
      articles: all.data.total,
      drafts: draft.data.total,
      pages: pages.data.length,
    }
  } catch (e) {
    /* 占位：拉取失败也不影响首页展示 */
  }
})
</script>

<template>
  <div>
    <div class="page-head">
      <h1>首页</h1>
    </div>

    <div class="card welcome">
      <h2>欢迎回来 👋</h2>
      <p class="muted">这里是 dawnop 控制台。先放点占位内容，后续可扩展为数据概览。</p>
    </div>

    <div class="stat-grid">
      <RouterLink to="/admin/articles" class="card stat">
        <div class="num">{{ stats.articles ?? '—' }}</div>
        <div class="label">文章总数</div>
      </RouterLink>
      <RouterLink to="/admin/articles" class="card stat">
        <div class="num">{{ stats.drafts ?? '—' }}</div>
        <div class="label">草稿</div>
      </RouterLink>
      <RouterLink to="/admin/pages" class="card stat">
        <div class="num">{{ stats.pages ?? '—' }}</div>
        <div class="label">页面</div>
      </RouterLink>
    </div>

    <div class="card quick">
      <h3>快捷入口</h3>
      <div class="links">
        <RouterLink to="/admin/articles/new">写文章</RouterLink>
        <RouterLink to="/admin/pages">页面管理</RouterLink>
        <RouterLink to="/admin/files">文件管理</RouterLink>
        <a href="/" target="_blank" rel="noopener">查看站点 ↗</a>
      </div>
    </div>
  </div>
</template>

<style scoped>
.welcome {
  margin-bottom: 16px;
}
.welcome h2 {
  margin: 0 0 6px;
  font-size: 1.15rem;
}
.welcome p {
  margin: 0;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}
.stat {
  text-decoration: none;
  color: var(--fg);
  transition: box-shadow 0.15s, transform 0.15s;
}
.stat:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
  transform: translateY(-2px);
}
.stat .num {
  font-size: 2rem;
  font-weight: 650;
  color: var(--accent);
}
.stat .label {
  margin-top: 4px;
  color: var(--muted);
  font-size: 0.9rem;
}
.quick h3 {
  margin: 0 0 12px;
  font-size: 1.02rem;
}
.quick .links {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
}
.quick .links a {
  color: var(--accent);
  text-decoration: none;
}
.quick .links a:hover {
  text-decoration: underline;
}
</style>
