<script setup>
// 前台文章列表项（首页 / 文章列表页 / 标签页共用）：
// 标题 + 摘要 + 「日期 · 约 N 分钟 · #标签」meta 行
import { RouterLink } from 'vue-router'
import { fmtDate } from '../utils/format'

defineProps({
  items: { type: Array, required: true },
})


function readMinutes(wc) {
  return Math.max(1, Math.round((wc || 0) / 300))
}
</script>

<template>
  <ul class="post-list">
    <li v-for="a in items" :key="a.id" class="post">
      <RouterLink :to="`/article/${a.slug}`" class="post-title">{{ a.title }}</RouterLink>
      <p v-if="a.summary" class="post-summary">{{ a.summary }}</p>
      <div class="post-meta">
        <span>{{ fmtDate(a.created_at) }}</span>
        <span class="dot">·</span>
        <span>约 {{ readMinutes(a.word_count) }} 分钟</span>
        <template v-if="a.tags && a.tags.length">
          <span class="dot">·</span>
          <RouterLink
            v-for="t in a.tags"
            :key="t.slug"
            :to="`/tag/${t.slug}`"
            class="post-tag"
          >#{{ t.name }}</RouterLink>
        </template>
      </div>
    </li>
  </ul>
</template>

<style scoped>
.post-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.post {
  padding: 28px 0;
  border-bottom: 1px solid var(--border);
}
.post:first-child {
  padding-top: 8px;
}
.post-title {
  display: inline-block;
  font-size: 1.45rem;
  font-weight: 650;
  line-height: 1.35;
  letter-spacing: -0.01em;
  text-decoration: none;
  color: var(--fg);
  transition: color 0.15s;
}
.post-title:hover {
  color: var(--accent);
}
.post-summary {
  color: var(--muted);
  margin: 10px 0 0;
  line-height: 1.7;
}
.post-meta {
  color: #8b949e;
  font-size: 0.85rem;
  margin-top: 12px;
}
.post-meta .dot {
  margin: 0 8px;
  color: #c9cdd4;
}
.post-tag {
  color: var(--muted);
  text-decoration: none;
  margin-right: 8px;
  transition: color 0.15s;
}
.post-tag:hover {
  color: var(--accent);
}
</style>
