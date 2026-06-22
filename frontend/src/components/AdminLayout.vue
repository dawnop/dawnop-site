<script setup>
import { computed } from 'vue'
import { RouterLink, RouterView, useRouter, useRoute } from 'vue-router'
import { auth } from '../store/auth'

const router = useRouter()
const route = useRoute()

// 文件管理用满铺布局（去掉内边距与最大宽度限制）
const fluid = computed(() => route.name === 'admin-files')

function logout() {
  auth.logout()
  router.push({ name: 'admin-login' })
}
</script>

<template>
  <div class="admin">
    <aside class="sidebar">
      <div class="brand">dawnop 后台</div>
      <nav>
        <RouterLink to="/admin/articles">文章管理</RouterLink>
        <RouterLink to="/admin/pages">页面管理</RouterLink>
        <RouterLink to="/admin/files">文件管理</RouterLink>
      </nav>
      <div class="bottom">
        <a href="/" target="_blank" rel="noopener" class="view-site">查看站点 ↗</a>
        <button class="logout" @click="logout">退出登录</button>
      </div>
    </aside>
    <main class="content" :class="{ fluid }">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.admin {
  display: flex;
  min-height: 100vh;
}
.sidebar {
  width: 210px;
  flex-shrink: 0;
  background: #0f172a;
  color: #cbd5e1;
  display: flex;
  flex-direction: column;
  padding: 20px 0;
}
.brand {
  font-weight: 700;
  color: #fff;
  padding: 4px 20px 20px;
  font-size: 1.05rem;
}
.sidebar nav {
  display: flex;
  flex-direction: column;
}
.sidebar nav a {
  color: #cbd5e1;
  text-decoration: none;
  padding: 11px 20px;
  font-size: 0.95rem;
}
.sidebar nav a:hover {
  background: #1e293b;
  color: #fff;
}
.sidebar nav a.router-link-active {
  background: #1e293b;
  color: #fff;
  box-shadow: inset 3px 0 0 #2563eb;
}
.bottom {
  margin-top: auto;
  padding: 16px 20px 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.view-site {
  color: #94a3b8;
  font-size: 0.85rem;
  text-decoration: none;
}
.logout {
  background: transparent;
  border: 1px solid #334155;
  color: #cbd5e1;
}
.logout:hover {
  background: #1e293b;
  border-color: #475569;
}
.content {
  flex: 1;
  padding: 32px 40px;
  max-width: 900px;
  background: #fff;
}
.content.fluid {
  padding: 0;
  max-width: none;
  /* 让子组件可按 100% 高度铺满 */
  display: flex;
  flex-direction: column;
  min-width: 0;
}
</style>
