<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Edit, Collection, FolderOpened, TopRight } from '@element-plus/icons-vue'
import { articlesApi, pagesApi } from '../../api'

const router = useRouter()
const stats = ref({ articles: null, drafts: null, pages: null })

const cards = [
  { key: 'articles', label: '文章总数', to: '/admin/articles' },
  { key: 'drafts', label: '草稿', to: '/admin/articles' },
  { key: 'pages', label: '页面', to: '/admin/pages' },
]

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
    /* 拉取失败也不影响首页展示 */
  }
})
</script>

<template>
  <div>
    <div class="page-head">
      <h1>首页</h1>
    </div>

    <el-card class="welcome" shadow="never">
      <h2>欢迎回来 👋</h2>
      <p class="muted">这里是 dawnop 控制台，可在左侧管理文章、页面与文件。</p>
    </el-card>

    <el-row :gutter="16" class="stat-row">
      <el-col v-for="c in cards" :key="c.label" :span="8">
        <el-card class="stat" shadow="hover" @click="router.push(c.to)">
          <el-statistic :value="stats[c.key] ?? 0" :title="c.label" />
        </el-card>
      </el-col>
    </el-row>

    <el-card class="quick" shadow="never">
      <template #header><span class="quick-title">快捷入口</span></template>
      <div class="links">
        <el-button :icon="Edit" @click="router.push('/admin/articles/new')">写文章</el-button>
        <el-button :icon="Collection" @click="router.push('/admin/pages')">页面管理</el-button>
        <el-button :icon="FolderOpened" @click="router.push('/admin/files')">文件管理</el-button>
        <el-button :icon="TopRight" tag="a" href="/" target="_blank" rel="noopener">查看站点</el-button>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.page-head {
  margin-bottom: 16px;
}
.page-head h1 {
  margin: 0;
  font-size: 1.3rem;
  font-weight: 600;
}
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
.muted {
  color: var(--muted);
}
.stat-row {
  margin-bottom: 16px;
}
.stat {
  cursor: pointer;
}
.quick-title {
  font-weight: 600;
}
.links {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
</style>
