<script setup>
import { RouterView } from 'vue-router'
import SiteHeader from './SiteHeader.vue'
</script>

<template>
  <SiteHeader />
  <main class="page">
    <RouterView />
  </main>
  <footer class="site-footer">© {{ new Date().getFullYear() }} dawnop</footer>
</template>

<style scoped>
.page {
  max-width: 820px;
  margin: 0 auto;
  padding: 32px 20px 64px;
  min-height: calc(100vh - 56px - 60px);
}
.site-footer {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #8b949e;
  font-size: 0.85rem;
  border-top: 1px solid #ebedf0;
}
</style>
