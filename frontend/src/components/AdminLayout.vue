<script setup>
import { ref, computed, watch } from 'vue'
import { RouterView, useRouter, useRoute } from 'vue-router'
import { Fold, Expand, House, Document, Collection, FolderOpened, MagicStick, PriceTag, Setting, Odometer, MoreFilled } from '@element-plus/icons-vue'
import { auth } from '../store/auth'
import { useIsMobile } from '../composables/useIsMobile'

const router = useRouter()
const route = useRoute()
const isMobile = useIsMobile()

// 侧栏折叠态（桌面：图标折叠，持久化）
const collapsed = ref(localStorage.getItem('dawnop_admin_collapsed') === '1')
// 移动端：侧栏改为抽屉，用独立开关
const drawerOpen = ref(false)

function toggle() {
  if (isMobile.value) {
    drawerOpen.value = !drawerOpen.value
    return
  }
  collapsed.value = !collapsed.value
  localStorage.setItem('dawnop_admin_collapsed', collapsed.value ? '1' : '0')
}

// 路由切换后自动收起移动抽屉（覆盖菜单点击、面包屑跳转等一切导航）
watch(() => route.path, () => { drawerOpen.value = false })

// 顶栏右上下拉（移动端把「查看站点 / 退出」收起）
function onTopCmd(cmd) {
  if (cmd === 'site') window.open('/', '_blank', 'noopener')
  else if (cmd === 'logout') logout()
}

// 分组导航（icon 用按需导入的图标组件引用）
const groups = [
  { label: '总览', items: [{ to: '/admin', label: '首页', icon: House }] },
  {
    label: '内容',
    items: [
      { to: '/admin/articles', label: '文章管理', icon: Document },
      { to: '/admin/tags', label: '标签管理', icon: PriceTag },
      { to: '/admin/pages', label: '页面管理', icon: Collection },
      { to: '/admin/viz', label: '可视化', icon: MagicStick },
    ],
  },
  {
    label: '存储',
    items: [{ to: '/admin/files', label: '文件管理', icon: FolderOpened }],
  },
  {
    label: '系统',
    items: [
      { to: '/admin/monitor', label: '监控', icon: Odometer },
      { to: '/admin/settings', label: '全局设置', icon: Setting },
    ],
  },
]

// 当前高亮项：首页精确匹配，其余匹配前缀（子路由如 /admin/articles/new 仍高亮文章管理）
const navTos = ['/admin/articles', '/admin/tags', '/admin/pages', '/admin/viz', '/admin/files', '/admin/monitor', '/admin/settings']
const activeMenu = computed(() => {
  if (route.path === '/admin') return '/admin'
  return navTos.find((t) => route.path === t || route.path.startsWith(t + '/')) || route.path
})

// 面包屑：带 to 的可点击跳转，末项为当前页不带链接
const crumbs = computed(() => {
  const home = { label: '首页', to: '/admin' }
  const map = {
    'admin-home': [{ label: '首页' }],
    'admin-articles': [home, { label: '文章管理' }],
    'admin-article-new': [home, { label: '文章管理', to: '/admin/articles' }, { label: '写文章' }],
    'admin-article-edit': [home, { label: '文章管理', to: '/admin/articles' }, { label: '编辑文章' }],
    'admin-tags': [home, { label: '标签管理' }],
    'admin-pages': [home, { label: '页面管理' }],
    'admin-page-new': [home, { label: '页面管理', to: '/admin/pages' }, { label: '新建页面' }],
    'admin-page-edit': [home, { label: '页面管理', to: '/admin/pages' }, { label: '编辑页面' }],
    'admin-viz': [home, { label: '可视化' }],
    'admin-viz-new': [home, { label: '可视化', to: '/admin/viz' }, { label: '新建可视化' }],
    'admin-viz-edit': [home, { label: '可视化', to: '/admin/viz' }, { label: '编辑可视化' }],
    'admin-files': [home, { label: '文件管理' }],
    'admin-monitor': [home, { label: '监控' }],
    'admin-settings': [home, { label: '全局设置' }],
  }
  return map[route.name] || []
})

// 文件管理满铺（无面包屑、无内边距）
const fluid = computed(() => route.name === 'admin-files')

function logout() {
  auth.logout()
  router.push({ name: 'admin-login' })
}
</script>

<template>
  <el-container class="admin">
    <!-- 顶栏 -->
    <el-header class="topbar">
      <div class="left">
        <el-icon class="toggle" :title="isMobile ? '菜单' : (collapsed ? '展开' : '收起')" @click="toggle">
          <Expand v-if="isMobile || collapsed" />
          <Fold v-else />
        </el-icon>
        <span class="brand"><img src="/logo.svg" alt="" class="brand-logo" /><span class="brand-text">dawnop 控制台</span></span>
        <el-breadcrumb v-if="crumbs.length" class="topbar-crumb" separator="/">
          <el-breadcrumb-item v-for="(c, i) in crumbs" :key="i" :to="c.to">
            {{ c.label }}
          </el-breadcrumb-item>
        </el-breadcrumb>
      </div>
      <div class="right">
        <template v-if="!isMobile">
          <a href="/" target="_blank" rel="noopener" class="topbar-link">查看站点 ↗</a>
          <span class="account">管理员</span>
          <el-button size="small" @click="logout">退出登录</el-button>
        </template>
        <el-dropdown v-else trigger="click" @command="onTopCmd">
          <el-icon class="topbar-more"><MoreFilled /></el-icon>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="site">查看站点 ↗</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </el-header>

    <el-container class="body">
      <!-- 桌面侧栏 -->
      <el-aside v-if="!isMobile" :width="collapsed ? '64px' : '208px'" class="sidebar">
        <el-menu :collapse="collapsed" :default-active="activeMenu" router :collapse-transition="false">
          <el-menu-item-group v-for="g in groups" :key="g.label">
            <template #title>{{ g.label }}</template>
            <el-menu-item v-for="it in g.items" :key="it.to" :index="it.to">
              <el-icon><component :is="it.icon" /></el-icon>
              <template #title>{{ it.label }}</template>
            </el-menu-item>
          </el-menu-item-group>
        </el-menu>
      </el-aside>

      <!-- 主区 -->
      <el-main class="main" :class="{ fluid }">
        <div class="view" :class="{ fluid }">
          <RouterView />
        </div>
      </el-main>
    </el-container>

    <!-- 移动端抽屉侧栏（路由切换后自动收起） -->
    <el-drawer v-model="drawerOpen" direction="ltr" :size="232" :with-header="false" class="nav-drawer">
      <el-menu :default-active="activeMenu" router :collapse-transition="false">
        <el-menu-item-group v-for="g in groups" :key="g.label">
          <template #title>{{ g.label }}</template>
          <el-menu-item v-for="it in g.items" :key="it.to" :index="it.to">
            <el-icon><component :is="it.icon" /></el-icon>
            <template #title>{{ it.label }}</template>
          </el-menu-item>
        </el-menu-item-group>
      </el-menu>
    </el-drawer>
  </el-container>
</template>

<style scoped>
.admin {
  height: 100vh;
  background: var(--layout-bg);
}

/* 顶栏 */
.topbar {
  height: 56px;
  background: #fff;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
}
.topbar .left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.toggle {
  font-size: 22px;
  color: #595959;
  cursor: pointer;
  width: 40px;
  height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  flex-shrink: 0;
}
.toggle:hover {
  background: #f0f2f5;
  color: var(--accent);
}
/* 顶栏面包屑：紧挨品牌，左侧竖线分隔 */
.topbar-crumb {
  display: flex;
  align-items: center;
  padding-left: 12px;
  border-left: 1px solid var(--border);
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
}
.brand {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-size: 1.05rem;
  color: #1f2329;
  flex-shrink: 0;
}
.brand-logo {
  width: 24px;
  height: 24px;
  display: block;
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

.body {
  height: calc(100vh - 56px);
}

/* 侧栏 */
.sidebar {
  background: #fff;
  border-right: 1px solid var(--border);
  transition: width 0.2s ease;
  overflow: hidden;
}
.sidebar .el-menu {
  border-right: none;
}

/* 主区：仅此处滚动，消除全局滚动条 */
.main {
  padding: 0;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
/* 主区留白随窗口宽度自适应：窄屏收紧，宽屏保持 28px */
.view {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px clamp(14px, 2.2vw, 28px) 32px;
}
/* 文件管理满铺 */
.view.fluid {
  padding: 20px clamp(14px, 2.2vw, 28px) 24px;
  display: flex;
  flex-direction: column;
}

/* 顶栏右上「更多」图标（移动端） */
.topbar-more {
  font-size: 20px;
  color: #595959;
  cursor: pointer;
  padding: 8px;
  border-radius: 6px;
}
.topbar-more:hover {
  background: #f0f2f5;
  color: var(--accent);
}

/* 抽屉侧栏样式挂在 assets/responsive.css（el-drawer teleport 到 body，scoped 命不中） */

@media (max-width: 768px) {
  .topbar {
    padding: 0 8px;
  }
  .topbar .left {
    gap: 6px;
  }
  /* 窄屏隐藏品牌文字（只留 logo），给面包屑腾地方 */
  .brand-text {
    display: none;
  }
  .topbar-crumb {
    padding-left: 8px;
  }
  .view {
    padding: 14px 14px 24px;
  }
  .view.fluid {
    padding: 12px 12px 16px;
  }
}
</style>
