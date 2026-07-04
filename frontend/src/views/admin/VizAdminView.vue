<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { MagicStick } from '@element-plus/icons-vue'
import { vizApi } from '../../api'
import HelpTip from '../../components/HelpTip.vue'
import { useColWidths } from '../../utils/colWidths'
import { useIsMobile } from '../../composables/useIsMobile'

const { colW, onHeaderDrag } = useColWidths('dawnop_colw_viz')

const router = useRouter()
const isMobile = useIsMobile()
const items = ref([])
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    const { data } = await vizApi.listAll()
    items.value = data
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

async function remove(v) {
  try {
    await ElMessageBox.confirm(
      `确定删除可视化「${v.name || v.slug}」？引用它的文章将显示加载失败。`,
      '删除可视化',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消', confirmButtonClass: 'el-button--danger' }
    )
  } catch (e) {
    return
  }
  await vizApi.remove(v.id)
  ElMessage.success('已删除')
  load()
}

function fmtDate(s) {
  return new Date(s).toLocaleDateString('zh-CN')
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <h1>可视化组件</h1>
      <el-button type="primary" :icon="MagicStick" @click="router.push('/admin/viz/new')">
        新建可视化
      </el-button>
    </div>
    <p class="hint">
      在后台直接写 Vue 组件、实时预览、保存即生效。引用格式
      <HelpTip>
        <div>文章里用如下围栏引用（标识单独成行）：</div>
        <pre style="margin: 6px 0 0; font: 12px/1.5 ui-monospace, Menlo, monospace; white-space: pre;">```viz
标识
```</pre>
      </HelpTip>
    </p>

    <el-card v-if="!isMobile" shadow="never">
      <el-table v-loading="loading" :data="items" row-key="id" border empty-text="还没有可视化组件" @header-dragend="onHeaderDrag">
        <el-table-column prop="slug" label="标识" :width="colW.slug || 240" show-overflow-tooltip>
          <template #default="{ row }"><code class="slug">{{ row.slug }}</code></template>
        </el-table-column>
        <el-table-column prop="name" label="名称" :width="colW.name || 240" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="{ muted: !row.name }">{{ row.name || '（未命名）' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="更新于" :width="colW['更新于'] || 130">
          <template #default="{ row }"><span class="muted">{{ fmtDate(row.updated_at) }}</span></template>
        </el-table-column>
        <el-table-column />
        <el-table-column label="操作" :width="colW['操作'] || 130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="router.push(`/admin/viz/${row.id}/edit`)">
              编辑
            </el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 移动端卡片列表 -->
    <div v-else v-loading="loading" class="m-list">
      <el-empty v-if="!items.length && !loading" description="还没有可视化组件" :image-size="80" />
      <el-card v-for="row in items" :key="row.id" shadow="never" class="m-card">
        <div class="m-card-head">
          <code class="slug">{{ row.slug }}</code>
          <span :class="{ muted: !row.name }" class="m-name">{{ row.name || '（未命名）' }}</span>
        </div>
        <div class="m-meta">更新于 {{ fmtDate(row.updated_at) }}</div>
        <div class="m-actions">
          <el-button size="small" @click="router.push(`/admin/viz/${row.id}/edit`)">编辑</el-button>
          <el-button size="small" type="danger" plain @click="remove(row)">删除</el-button>
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.page-head h1 {
  margin: 0;
  font-size: 1.3rem;
  font-weight: 600;
}
.hint {
  margin: 0 0 16px;
  color: var(--muted);
  font-size: 0.85rem;
}
.hint code,
.slug {
  background: #f0f1f3;
  padding: 0.1em 0.4em;
  border-radius: 4px;
  font-size: 0.85em;
}
.muted {
  color: var(--muted);
}

/* 移动端卡片列表 */
.m-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.m-card :deep(.el-card__body) {
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.m-card-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}
.m-name {
  font-weight: 600;
}
.m-meta {
  font-size: 0.8rem;
  color: var(--muted);
}
.m-actions {
  display: flex;
  gap: 8px;
}
.m-actions > .el-button {
  flex: 1;
}

@media (max-width: 768px) {
  .page-head {
    flex-wrap: wrap;
    gap: 10px;
  }
  .page-head h1 {
    font-size: 1.15rem;
  }
}
</style>
