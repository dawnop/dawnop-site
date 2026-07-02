<script setup>
import { ref, onMounted } from 'vue'
import { articlesApi } from '../api'
import PostList from '../components/PostList.vue'

const items = ref([])
const total = ref(0)
const page = ref(1)
const size = 10
const loading = ref(true)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await articlesApi.listPublished(page.value, size)
    items.value = data.items
    total.value = data.total
  } catch (e) {
    error.value = '加载失败'
  } finally {
    loading.value = false
  }
}

function go(p) {
  page.value = p
  load()
}

onMounted(load)
</script>

<template>
  <div>
    <el-skeleton v-if="loading" :rows="5" animated />
    <el-empty v-else-if="error" :description="error" />
    <el-empty v-else-if="items.length === 0" description="还没有文章" />

    <PostList v-else :items="items" />

    <div v-if="total > size" class="pager">
      <el-pagination
        background
        layout="prev, pager, next"
        :total="total"
        :page-size="size"
        :current-page="page"
        @current-change="go"
      />
    </div>
  </div>
</template>

<style scoped>
.pager {
  display: flex;
  justify-content: center;
  margin-top: 28px;
}
</style>
