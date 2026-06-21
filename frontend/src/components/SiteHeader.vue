<script setup>
import { RouterLink, useRouter } from 'vue-router'
import { auth } from '../store/auth'

const router = useRouter()

function logout() {
  auth.logout()
  router.push({ name: 'home' })
}
</script>

<template>
  <header class="site-header">
    <div class="inner">
      <RouterLink to="/" class="brand">dawnop</RouterLink>
      <nav class="nav">
        <RouterLink to="/">首页</RouterLink>
        <RouterLink to="/about">关于</RouterLink>
        <template v-if="auth.isAuthenticated">
          <RouterLink to="/admin/articles">文章管理</RouterLink>
          <RouterLink to="/admin/files">文件管理</RouterLink>
          <a href="#" class="link" @click.prevent="logout">退出</a>
        </template>
        <RouterLink v-else to="/login">登录</RouterLink>
      </nav>
    </div>
  </header>
</template>

<style scoped>
.site-header {
  border-bottom: 1px solid #ebedf0;
  background: #fff;
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
  font-weight: 700;
  font-size: 1.2rem;
  color: #1a1a1a;
  text-decoration: none;
  letter-spacing: 0.5px;
}
.nav {
  display: flex;
  gap: 18px;
  align-items: center;
}
.nav a {
  color: #57606a;
  text-decoration: none;
  font-size: 0.95rem;
}
.nav a:hover,
.nav a.router-link-active {
  color: #1a1a1a;
}
</style>
