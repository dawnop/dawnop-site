<script setup>
import { ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { articlesApi, tagsApi } from '../api'
import PostList from '../components/PostList.vue'

const route = useRoute()
const items = ref([])
const total = ref(0)
const page = ref(1)
const size = 10
const loading = ref(true)
const error = ref('')
const tagName = ref('')

async function load() {
  loading.value = true
  error.value = ''
  const slug = route.params.slug
  try {
    // 标签名走独立接口，空标签/全草稿标签也能正确显示名字
    const [tagResp, listResp] = await Promise.all([
      tagsApi.get(slug),
      articlesApi.listPublished(page.value, size, slug),
    ])
    tagName.value = tagResp.data.name
    items.value = listResp.data.items
    total.value = listResp.data.total
    document.title = `#${tagName.value} · dawnop`
  } catch (e) {
    error.value = e?.response?.status === 404 ? '标签不存在' : '加载失败'
  } finally {
    loading.value = false
  }
}

function go(p) {
  page.value = p
  load()
}

watch(() => route.params.slug, () => { page.value = 1; load() }, { immediate: true })
</script>

<template>
  <div>
    <div class="tag-head">
      <RouterLink to="/tags" class="back">← 全部标签</RouterLink>
      <h1 class="tag-title">
        <span class="hash">#</span>{{ tagName }}
        <span v-if="!loading" class="count">{{ total }} 篇</span>
      </h1>
    </div>

    <el-skeleton v-if="loading" :rows="5" animated />
    <el-empty v-else-if="error" :description="error" />
    <el-empty v-else-if="items.length === 0" description="该标签下暂无文章" />

    <PostList v-else :items="items" class="post-list-wrap" />

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
.tag-head {
  margin-bottom: 12px;
}
.back {
  font-size: 0.88rem;
  color: var(--muted);
  text-decoration: none;
}
.back:hover {
  color: var(--accent);
}
.tag-title {
  font-size: 1.7rem;
  font-weight: 700;
  margin: 8px 0 0;
  letter-spacing: -0.01em;
}
.tag-title .hash {
  color: var(--accent);
}
.tag-title .count {
  font-size: 0.9rem;
  font-weight: 400;
  color: var(--muted);
  margin-left: 10px;
}
.post-list-wrap {
  margin-top: 8px;
}
.pager {
  display: flex;
  justify-content: center;
  margin-top: 28px;
}
</style>
