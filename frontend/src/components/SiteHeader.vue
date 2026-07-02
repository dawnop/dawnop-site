<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, RouterLink } from 'vue-router'
import { pagesApi } from '../api'

const route = useRoute()

// 导航完全由后台「页面管理」驱动（含内置的首页/标签页），每项自带跳转 path。
// 接口返回前先放一个首页兜底，避免首屏导航空白。
const FALLBACK = [{ id: 0, title: '首页', path: '/' }]
const navPages = ref(FALLBACK)

// el-menu 高亮：精确到当前路径（文章详情页不属于任何导航项，则均不高亮）
const activePath = computed(() => route.path)

onMounted(async () => {
  try {
    const { data } = await pagesApi.nav()
    if (data.length) navPages.value = data
  } catch (e) {
    /* 保留兜底导航 */
  }
})
</script>

<template>
  <header class="site-header">
    <div class="inner">
      <RouterLink to="/" class="brand">
        <img src="/logo.svg" alt="dawnop" class="logo" />
        <span>dawnop</span>
      </RouterLink>
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
    </div>
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
</style>
