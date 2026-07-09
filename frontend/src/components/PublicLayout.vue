<script setup>
import { RouterView } from 'vue-router'
import SiteHeader from './SiteHeader.vue'
import mpsIcon from '../assets/beian-mps.png'

// 备案号走构建时环境变量（.env.local，不入库）：开源仓库不携带个人备案信息，未配置则页脚不渲染
const icp = import.meta.env.VITE_BEIAN_ICP || ''
const mps = import.meta.env.VITE_BEIAN_MPS || ''
const mpsLink = mps
  ? `https://beian.mps.gov.cn/#/query/webSearch?code=${mps.replace(/\D/g, '')}`
  : ''
</script>

<template>
  <SiteHeader />
  <main class="page">
    <RouterView />
  </main>
  <footer class="site-footer">
    <span>© {{ new Date().getFullYear() }} dawnop</span>
    <a v-if="icp" class="beian" href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">
      {{ icp }}
    </a>
    <a v-if="mps" class="beian" :href="mpsLink" target="_blank" rel="noreferrer">
      <img :src="mpsIcon" alt="公安备案" />
      {{ mps }}
    </a>
  </footer>
</template>

<style scoped>
.page {
  max-width: 820px;
  margin: 0 auto;
  padding: 32px 20px 64px;
  min-height: calc(100vh - 56px - 60px);
}
.site-footer {
  min-height: 60px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 4px 18px;
  padding: 10px 16px;
  color: #8b949e;
  font-size: 0.85rem;
  border-top: 1px solid #ebedf0;
}
.beian {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: inherit;
  text-decoration: none;
}
.beian:hover {
  color: var(--accent);
}
.beian img {
  width: 14px;
  height: 14px;
}
</style>
