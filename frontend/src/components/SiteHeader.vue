<script setup>
import { ref, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { pagesApi } from '../api'

// 导航 = 固定「首页」+ 后台「显示在导航」的页面（按顺序）
const navPages = ref([])

onMounted(async () => {
  try {
    const { data } = await pagesApi.nav()
    navPages.value = data
  } catch (e) {
    navPages.value = []
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
      <nav class="nav">
        <RouterLink to="/">首页</RouterLink>
        <RouterLink v-for="p in navPages" :key="p.id" :to="`/p/${p.slug}`">
          {{ p.title }}
        </RouterLink>
      </nav>
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
.nav {
  display: flex;
  gap: 18px;
  align-items: center;
  flex-wrap: wrap;
}
.nav a {
  color: #57606a;
  text-decoration: none;
  font-size: 0.95rem;
}
.nav a:hover,
.nav a.router-link-exact-active {
  color: #1a1a1a;
}
</style>
