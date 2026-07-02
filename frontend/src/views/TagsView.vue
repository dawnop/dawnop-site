<script setup>
import { ref, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { tagsApi } from '../api'

const items = ref([])
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    const { data } = await tagsApi.list()
    items.value = data
  } catch (e) {
    error.value = '加载失败'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div>
    <h1 class="page-title">标签</h1>

    <el-skeleton v-if="loading" :rows="3" animated />
    <el-empty v-else-if="error" :description="error" />
    <el-empty v-else-if="items.length === 0" description="还没有标签" />

    <div v-else class="tag-grid">
      <RouterLink v-for="t in items" :key="t.slug" :to="`/tag/${t.slug}`" class="tag-item">
        <span class="hash">#</span>{{ t.name }}
        <span class="count">{{ t.count }}</span>
      </RouterLink>
    </div>
  </div>
</template>

<style scoped>
.page-title {
  font-size: 1.7rem;
  font-weight: 700;
  letter-spacing: -0.01em;
  margin: 0 0 20px;
}
.tag-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.tag-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--fg);
  font-size: 0.95rem;
  text-decoration: none;
  transition: border-color 0.15s, color 0.15s;
}
.tag-item:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.tag-item .hash {
  color: var(--accent);
}
.tag-item .count {
  font-size: 0.8rem;
  color: var(--muted);
}
</style>
