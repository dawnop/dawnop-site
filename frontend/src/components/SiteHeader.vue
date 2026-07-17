<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, RouterLink } from 'vue-router'
import { Search } from '@element-plus/icons-vue'
import { pagesApi } from '../api'
import SearchModal from './SearchModal.vue'

const route = useRoute()

// 导航完全由后台「页面管理」驱动（含内置的首页/标签页），每项自带跳转 path。
// 接口返回前先放一个首页兜底，避免首屏导航空白。
const FALLBACK = [{ id: 0, title: '首页', path: '/' }]
const navPages = ref(FALLBACK)

// el-menu 高亮：精确到当前路径（文章详情页不属于任何导航项，则均不高亮）
const activePath = computed(() => route.path)

// 顶栏搜索：触发 ⌘K 命令面板
const searchOpen = ref(false)
const isMac = /Mac|iPhone|iPad|iPod/.test(navigator.userAgent)
const modKey = isMac ? '⌘' : 'Ctrl'

function onKeydown(e) {
  if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault()
    searchOpen.value = !searchOpen.value
  }
}

onMounted(async () => {
  window.addEventListener('keydown', onKeydown)
  try {
    const { data } = await pagesApi.nav()
    if (data.length) navPages.value = data
  } catch (e) {
    /* 保留兜底导航 */
  }
})
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <header class="site-header">
    <div class="inner">
      <RouterLink to="/" class="brand">
        <img src="/logo.svg" alt="dawnop" class="logo" />
        <span>dawnop</span>
      </RouterLink>
      <div class="right">
        <el-menu
          mode="horizontal"
          :router="true"
          :default-active="activePath"
          :ellipsis="false"
          class="nav"
        >
          <el-menu-item v-for="p in navPages" :key="p.id" :index="p.path">
            {{ p.title }}
          </el-menu-item>
        </el-menu>
        <button class="search-trigger" type="button" @click="searchOpen = true" aria-label="搜索">
          <el-icon class="st-icon"><Search /></el-icon>
          <span class="st-label">搜索</span>
          <kbd class="st-kbd">{{ modKey }}K</kbd>
        </button>
      </div>
    </div>
    <SearchModal v-model="searchOpen" />
  </header>
</template>

<style scoped>
.site-header {
  border-bottom: 1px solid #ebedf0;
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: saturate(180%) blur(8px);
  position: sticky;
  top: 0;
  z-index: 50;
}
.inner {
  max-width: 820px;
  margin: 0 auto;
  padding: 0 20px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.brand {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  font-weight: 700;
  font-size: 1.2rem;
  color: #1a1a1a;
  text-decoration: none;
  letter-spacing: 0.5px;
}
.brand .logo {
  width: 28px;
  height: 28px;
  display: block;
}
.right {
  display: flex;
  align-items: center;
  gap: 12px;
}
/* el-menu 调成轻量横向导航：透明底、无自身下边框（边框在 header 上）、贴右 */
.nav.el-menu--horizontal {
  border-bottom: none;
  background: transparent;
  height: 55px;
}
.nav.el-menu--horizontal > .el-menu-item {
  font-size: 0.95rem;
  height: 55px;
  line-height: 55px;
}
/* 搜索触发按钮：仿命令面板入口 */
.search-trigger {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding: 0 8px 0 10px;
  border: 1px solid #e6e8eb;
  border-radius: 8px;
  background: #f7f8fa;
  color: #9aa0a6;
  cursor: pointer;
  font-size: 0.88rem;
  transition:
    border-color 0.15s,
    background 0.15s;
}
.search-trigger:hover {
  border-color: #d3d6da;
  background: #fff;
}
.st-icon {
  font-size: 15px;
}
.st-label {
  color: #6b7178;
}
.st-kbd {
  font-family: inherit;
  font-size: 0.72rem;
  line-height: 1;
  color: #9aa0a6;
  background: #fff;
  border: 1px solid #e3e5e8;
  border-radius: 5px;
  padding: 3px 5px;
}
@media (max-width: 560px) {
  .st-label,
  .st-kbd {
    display: none;
  }
  .search-trigger {
    height: 40px;
    width: 40px;
    justify-content: center;
    padding: 0;
  }
  .st-icon {
    font-size: 18px;
  }
}
</style>
