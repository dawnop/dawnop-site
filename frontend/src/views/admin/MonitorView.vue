<script setup>
// 监控总览：拉取聚合数据、分发到三张卡片子组件（腾讯云+本机 / Vault / 七牛）。
// 卡片内部各自派生 + 渲染；本视图只管取数、缓存提示、瀑布流容器。
import { ref, computed, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { monitorApi } from '../../api'
import ServerCard from '../../components/monitor/ServerCard.vue'
import VaultCard from '../../components/monitor/VaultCard.vue'
import QiniuCard from '../../components/monitor/QiniuCard.vue'

const data = ref(null)
const loading = ref(true)
const refreshing = ref(false)
const updatedAt = ref('')

async function load(refresh = false) {
  refresh ? (refreshing.value = true) : (loading.value = true)
  try {
    const { data: d } = await monitorApi.overview(refresh)
    data.value = d
    updatedAt.value = new Date().toLocaleTimeString('zh-CN')
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
    refreshing.value = false
  }
}
onMounted(() => load(false))

// 分块取数（各块独立容错，缺某块不影响其余）
const server = computed(() => data.value?.server || null)
const lh = computed(() => data.value?.lighthouse || null)
const qn = computed(() => data.value?.qiniu || null)
const vault = computed(() => data.value?.vault || null)
</script>

<template>
  <div v-loading="loading">
    <div class="page-head">
      <p class="hint">
        资源用量总览 · 三方数据缓存约 2 分钟
        <span v-if="updatedAt" class="muted">（{{ updatedAt }} 更新）</span>
      </p>
      <el-button :icon="Refresh" :loading="refreshing" @click="load(true)">刷新</el-button>
    </div>

    <div v-if="data" class="cards">
      <ServerCard :server="server" :lh="lh" />
      <!-- Vault 放在七牛之前，让最高的七牛卡片单独占一列，瀑布流更均衡 -->
      <VaultCard :vault="vault" />
      <QiniuCard :qn="qn" />
    </div>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.page-head .el-button {
  flex-shrink: 0;
}
.hint {
  margin: 0;
  color: var(--muted);
  font-size: 0.85rem;
}
.muted {
  color: var(--muted);
}

/* 瀑布流：CSS 多列布局，卡片按列紧凑堆叠、不留行对齐的空隙 */
.cards {
  column-count: 2;
  column-gap: 16px;
}

@media (max-width: 768px) {
  .cards {
    column-count: 1;
  }
  .page-head {
    flex-wrap: wrap;
    gap: 10px;
  }
  .page-head .el-button {
    width: 100%;
  }
}
</style>
