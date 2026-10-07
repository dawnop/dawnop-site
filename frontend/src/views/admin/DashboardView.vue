<script setup>
import { ref, onMounted } from 'vue'
import { Edit, Document, FolderOpened, Link, Setting } from '@element-plus/icons-vue'
import { articlesApi, pagesApi } from '../../api'
const stats = ref({})
const loading = ref(false)
const error = ref(false)
const cards = [
  { key: 'articles', label: '文章', to: '/admin/articles' },
  { key: 'drafts', label: '草稿', to: '/admin/articles' },
  { key: 'pages', label: '页面', to: '/admin/pages' },
  { key: 'views', label: '浏览量', to: '/admin/articles' },
]
const links = [
  { label: '写文章', to: '/admin/articles/new', icon: Edit },
  { label: '剪贴板', to: '/admin/pastes', icon: Document },
  { label: '文件', to: '/admin/files', icon: FolderOpened },
  { label: '上传链接', to: '/admin/drops', icon: Link },
  { label: '全局设置', to: '/admin/settings', icon: Setting },
]
async function load() {
  loading.value = true
  error.value = false
  try {
    const [s, pages] = await Promise.all([articlesApi.stats(), pagesApi.listAll()])
    stats.value = {
      articles: s.data.total,
      drafts: s.data.drafts,
      pages: pages.data.length,
      views: s.data.total_views,
    }
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>
<template>
  <div>
    <div v-loading="loading" class="stat-grid">
      <router-link v-for="card in cards" :key="card.key" :to="card.to" class="stat-card card">
        <span class="stat-label">{{ card.label }}</span>
        <strong>{{ stats[card.key]?.toLocaleString() ?? '—' }}</strong>
      </router-link>
    </div>
    <p v-if="error" role="alert" class="load-error">
      统计加载失败 <el-button text @click="load">重试</el-button>
    </p>
    <section class="card quick">
      <h2>快捷入口</h2>
      <div class="links">
        <router-link v-for="link in links" :key="link.to" :to="link.to">
          <el-icon><component :is="link.icon" /></el-icon><span>{{ link.label }}</span
          ><span class="arrow" aria-hidden="true">↗</span>
        </router-link>
      </div>
    </section>
  </div>
</template>
<style scoped>
.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}
.stat-card {
  display: flex;
  flex-direction: column;
  gap: 16px;
  text-decoration: none;
  color: var(--fg);
  transition: border-color 0.15s;
}
.stat-card:hover {
  border-color: var(--accent);
}
.stat-label {
  color: var(--muted);
  font-size: 13px;
}
.stat-card strong {
  font-size: 30px;
  line-height: 1.2;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.quick h2 {
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 16px;
}
.links {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.links a {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: 6px;
  text-decoration: none;
  color: var(--fg);
  font-size: 14px;
}
.links a:hover {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: #bae0ff;
}
.links .el-icon {
  color: var(--accent);
  font-size: 18px;
}
.arrow {
  margin-left: auto;
  color: var(--muted);
}
.load-error {
  color: var(--muted);
  font-size: 13px;
}
@media (max-width: 1000px) {
  .links {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 768px) {
  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  .stat-card strong {
    font-size: 26px;
  }
  .links {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
