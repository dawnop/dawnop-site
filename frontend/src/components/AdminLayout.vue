<script setup>
import { ref, computed } from 'vue'
import { RouterLink, RouterView, useRouter, useRoute } from 'vue-router'
import { auth } from '../store/auth'

const router = useRouter()
const route = useRoute()

// 侧栏折叠态（持久化）
const collapsed = ref(localStorage.getItem('dawnop_admin_collapsed') === '1')
function toggle() {
  collapsed.value = !collapsed.value
  localStorage.setItem('dawnop_admin_collapsed', collapsed.value ? '1' : '0')
}

// 分组导航
const groups = [
  {
    label: '总览',
    items: [{ to: '/admin', label: '首页', icon: 'home', exact: true }],
  },
  {
    label: '内容',
    items: [
      { to: '/admin/articles', label: '文章管理', icon: 'doc' },
      { to: '/admin/pages', label: '页面管理', icon: 'layers' },
    ],
  },
  {
    label: '存储',
    items: [{ to: '/admin/files', label: '文件管理', icon: 'folder' }],
  },
]

// 行内 SVG 图标（描边风，折叠态只显示图标）
const icons = {
  home: `<svg viewBox="0 0 24 24" width="18" height="18"><path d="M4 11l8-7 8 7M6 10v9h12v-9" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>`,
  doc: `<svg viewBox="0 0 24 24" width="18" height="18"><path d="M6 2h9l5 5v15H6z" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M14 2v6h6" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>`,
  layers: `<svg viewBox="0 0 24 24" width="18" height="18"><path d="M12 3l9 5-9 5-9-5 9-5z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M3 13l9 5 9-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>`,
  folder: `<svg viewBox="0 0 24 24" width="18" height="18"><path d="M3 6h6l2 2h10v11H3z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>`,
}

// 面包屑：带 to 的可点击跳转（用于编辑页返回列表），末项为当前页不带链接
const crumbs = computed(() => {
  const home = { label: '首页', to: '/admin' }
  const map = {
    'admin-home': [{ label: '首页' }],
    'admin-articles': [home, { label: '文章管理' }],
    'admin-article-new': [home, { label: '文章管理', to: '/admin/articles' }, { label: '写文章' }],
    'admin-article-edit': [home, { label: '文章管理', to: '/admin/articles' }, { label: '编辑文章' }],
    'admin-pages': [home, { label: '页面管理' }],
    'admin-page-new': [home, { label: '页面管理', to: '/admin/pages' }, { label: '新建页面' }],
    'admin-page-edit': [home, { label: '页面管理', to: '/admin/pages' }, { label: '编辑页面' }],
    'admin-files': [home, { label: '文件管理' }],
  }
  return map[route.name] || []
})

// 文件管理满铺（无面包屑、无内边距）
const fluid = computed(() => route.name === 'admin-files')

// 当前分区高亮（含子路由，如 /admin/articles/new 仍高亮“文章管理”）；
// exact 项（首页 /admin）仅精确匹配，避免被所有子路由点亮
function isActive(to, exact = false) {
  if (exact) return route.path === to
  return route.path === to || route.path.startsWith(to + '/')
}

function logout() {
  auth.logout()
  router.push({ name: 'admin-login' })
}
</script>

<template>
  <div class="admin" :class="{ collapsed }">
    <!-- 顶栏 -->
    <header class="topbar">
      <div class="left">
        <button class="icon-btn" @click="toggle" :title="collapsed ? '展开' : '收起'">
          <svg viewBox="0 0 24 24" width="18" height="18"><path d="M3 6h18M3 12h18M3 18h18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>
        </button>
        <span class="brand"><img src="/logo.svg" alt="" class="brand-logo" />dawnop 控制台</span>
      </div>
      <div class="right">
        <a href="/" target="_blank" rel="noopener" class="topbar-link">查看站点 ↗</a>
        <span class="account">管理员</span>
        <button class="logout" @click="logout">退出登录</button>
      </div>
    </header>

    <div class="body">
      <!-- 侧栏 -->
      <aside class="sidebar">
        <nav>
          <div v-for="g in groups" :key="g.label" class="group">
            <div class="group-label">{{ g.label }}</div>
            <RouterLink
              v-for="it in g.items"
              :key="it.to"
              :to="it.to"
              class="nav-item"
              :class="{ active: isActive(it.to, it.exact) }"
              :title="it.label"
            >
              <span class="ico" v-html="icons[it.icon]"></span>
              <span class="txt">{{ it.label }}</span>
            </RouterLink>
          </div>
        </nav>
      </aside>

      <!-- 主区 -->
      <main class="main" :class="{ fluid }">
        <nav v-if="!fluid && crumbs.length" class="breadcrumb">
          <template v-for="(c, i) in crumbs" :key="i">
            <span v-if="i > 0" class="sep">/</span>
            <RouterLink v-if="c.to" :to="c.to">{{ c.label }}</RouterLink>
            <span v-else class="crumb">{{ c.label }}</span>
          </template>
        </nav>
        <div class="view" :class="{ fluid }">
          <RouterView />
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
/* 锁定视口高度，内部只让 .view 滚动，消除全局/body 滚动条 */
.admin {
  height: 100vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: var(--layout-bg);
}

/* 顶栏 */
.topbar {
  height: 56px;
  flex-shrink: 0;
  background: #fff;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  position: sticky;
  top: 0;
  z-index: 20;
}
.topbar .left {
  display: flex;
  align-items: center;
  gap: 14px;
}
.brand {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-size: 1.05rem;
  color: #1f2329;
}
.brand-logo {
  width: 24px;
  height: 24px;
  display: block;
}
/* .topbar 前缀提高优先级，压过 admin.css 的 .admin button 基础样式 */
.topbar .icon-btn {
  border: none;
  background: transparent;
  padding: 6px;
  border-radius: 6px;
  color: #595959;
  display: inline-flex;
}
.topbar .icon-btn:hover {
  background: #f0f2f5;
  color: var(--accent);
}
.topbar .right {
  display: flex;
  align-items: center;
  gap: 16px;
}
.topbar-link {
  color: var(--muted);
  font-size: 0.88rem;
  text-decoration: none;
}
.topbar-link:hover {
  color: var(--accent);
}
.account {
  font-size: 0.9rem;
  color: #1f2329;
}
.logout {
  padding: 5px 12px;
  font-size: 0.88rem;
}

.body {
  flex: 1;
  display: flex;
  min-height: 0;
}

/* 侧栏 */
.sidebar {
  width: 208px;
  flex-shrink: 0;
  background: #fff;
  border-right: 1px solid var(--border);
  padding: 12px 0;
  transition: width 0.18s ease;
  overflow: hidden;
}
.admin.collapsed .sidebar {
  width: 60px;
}
.group {
  margin-bottom: 8px;
}
.group-label {
  font-size: 0.72rem;
  color: #b0b3b8;
  padding: 8px 20px 4px;
  letter-spacing: 0.04em;
  white-space: nowrap;
}
.admin.collapsed .group-label {
  visibility: hidden;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 20px;
  color: #4e5969;
  text-decoration: none;
  font-size: 0.95rem;
  border-left: 3px solid transparent;
  white-space: nowrap;
}
.nav-item .ico {
  display: inline-flex;
  flex-shrink: 0;
  color: #86909c;
}
.nav-item:hover {
  background: #f7f8fa;
  color: var(--accent);
}
.nav-item:hover .ico {
  color: var(--accent);
}
/* 仅用自管的 .active（isActive 控制）：避免 RouterLink 给父级 /admin 自动加的
   router-link-active 把「首页」在所有子路由下都点亮 */
.nav-item.active {
  background: var(--accent-soft);
  color: var(--accent);
  border-left-color: var(--accent);
  font-weight: 500;
}
.nav-item.active .ico {
  color: var(--accent);
}
.admin.collapsed .nav-item .txt {
  display: none;
}
.admin.collapsed .nav-item {
  justify-content: center;
  padding: 10px 0;
  gap: 0;
}

/* 主区 */
.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.breadcrumb {
  padding: 14px 28px 0;
  font-size: 0.85rem;
  color: var(--muted);
}
.breadcrumb a {
  color: var(--muted);
  text-decoration: none;
}
.breadcrumb a:hover {
  color: var(--accent);
}
.breadcrumb .sep {
  margin: 0 8px;
  color: #c9cdd4;
}
.breadcrumb .crumb {
  color: #1f2329;
}
/* 唯一的滚动区：内容超高时只有这里出现滚动条 */
.view {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 28px 32px;
}
/* 文件管理满铺 */
.main.fluid {
  min-height: 0;
}
/* 文件管理：保留正常内边距（与其他页一致），但让内容撑满剩余高度 */
.view.fluid {
  flex: 1;
  padding: 20px 28px 24px;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
</style>
