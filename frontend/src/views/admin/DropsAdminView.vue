<script setup>
// 上传链接（drop）管理：列出全部链接，可吊销或删除。创建入口在文件管理器（文件夹右键 / 工具栏），
// 因为链接总是绑定到某个目录。明文 token 只在创建时显示一次，这里拿不到，也不展示。
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { RefreshRight, MoreFilled } from '@element-plus/icons-vue'
import { fmApi } from '../../api'
import { useColWidths } from '../../composables/useColWidths'
import { useIsMobile } from '../../composables/useIsMobile'
import { confirmDanger } from '../../utils/confirm'
import { fmtBytes, fmtDateTime } from '../../utils/format'

const { colW, onHeaderDrag } = useColWidths('dawnop_colw_drops')
const isMobile = useIsMobile()

const items = ref([])
const loading = ref(true)

// 状态由后端算（active | expired | revoked | exhausted | dir_missing），这里只管文案与颜色
const STATUS = {
  active: { text: '有效', type: 'success' },
  expired: { text: '已过期', type: 'info' },
  revoked: { text: '已吊销', type: 'info' },
  exhausted: { text: '额度用完', type: 'warning' },
  dir_missing: { text: '目录已不存在', type: 'danger' },
}
const statusOf = (s) => STATUS[s] || { text: s || '未知', type: 'info' }
const dirText = (rel) => (rel ? `/${rel}` : '/')

async function load() {
  loading.value = true
  try {
    items.value = await fmApi.listDrops()
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

const nameOf = (row) => (row.label ? `「${row.label}」` : `指向 ${dirText(row.dir)} 的链接`)

async function revoke(row) {
  const ok = await confirmDanger(
    `吊销后${nameOf(row)}立即失效，持有者无法再上传；已上传的文件不受影响。此操作不可撤销。`,
    '吊销上传链接',
    { confirmText: '吊销' },
  )
  if (!ok) return
  try {
    await fmApi.revokeDrop(row.id)
    ElMessage.success('已吊销')
  } catch {
    // 失败提示由 axios 拦截器统一给
  }
  load()
}

async function remove(row) {
  const ok = await confirmDanger(
    `删除${nameOf(row)}的记录，链接随之失效；已上传的文件保留在原目录。`,
    '删除上传链接',
  )
  if (!ok) return
  try {
    await fmApi.deleteDrop(row.id)
    ElMessage.success('已删除')
  } catch {
    // 失败提示由 axios 拦截器统一给
  }
  load()
}

function rowCmd(cmd, row) {
  if (cmd === 'revoke') revoke(row)
  else if (cmd === 'delete') remove(row)
}

onMounted(load)
</script>

<template>
  <div>
    <el-card shadow="never">
      <div class="toolbar">
        <span class="muted total">共 {{ items.length }} 个上传链接</span>
        <span class="muted tip">在「文件管理」里右键文件夹即可创建</span>
        <el-button class="tb-refresh" :icon="RefreshRight" @click="load">刷新</el-button>
      </div>

      <el-table
        v-if="!isMobile"
        v-loading="loading"
        :data="items"
        border
        empty-text="还没有上传链接"
        @header-dragend="onHeaderDrag"
      >
        <el-table-column label="标签" :width="colW['标签'] || 180" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.label">{{ row.label }}</span>
            <span v-else class="muted">未命名</span>
          </template>
        </el-table-column>
        <el-table-column label="目录" :width="colW['目录'] || 200" show-overflow-tooltip>
          <template #default="{ row }">{{ dirText(row.dir) }}</template>
        </el-table-column>
        <el-table-column label="状态" :width="colW['状态'] || 120">
          <template #default="{ row }">
            <el-tag :type="statusOf(row.status).type" size="small" disable-transitions>
              {{ statusOf(row.status).text }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="已用 / 上限" :width="colW['已用 / 上限'] || 210">
          <template #default="{ row }">
            <div class="usage">
              <span>{{ row.used_files }} / {{ row.max_files }} 个</span>
              <span class="muted"
                >{{ fmtBytes(row.used_bytes) }} / {{ fmtBytes(row.max_total_bytes) }}</span
              >
            </div>
          </template>
        </el-table-column>
        <el-table-column label="单文件上限" :width="colW['单文件上限'] || 110">
          <template #default="{ row }">{{ fmtBytes(row.max_file_bytes) }}</template>
        </el-table-column>
        <el-table-column label="过期时间" :width="colW['过期时间'] || 160">
          <template #default="{ row }">{{ fmtDateTime(row.expires_at) }}</template>
        </el-table-column>
        <el-table-column label="创建时间" :width="colW['创建时间'] || 160">
          <template #default="{ row }">{{ fmtDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column />
        <el-table-column label="操作" :width="colW['操作'] || 140" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :disabled="row.status === 'revoked'" @click="revoke(row)"
              >吊销</el-button
            >
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 移动端卡片列表 -->
      <div v-else v-loading="loading" class="m-list">
        <el-empty v-if="!items.length && !loading" description="还没有上传链接" :image-size="80" />
        <div v-for="row in items" :key="row.id" class="m-row">
          <div class="m-info">
            <div class="m-head">
              <span class="m-label">{{ row.label || '未命名' }}</span>
              <el-tag :type="statusOf(row.status).type" size="small" disable-transitions>
                {{ statusOf(row.status).text }}
              </el-tag>
            </div>
            <div class="muted m-line">{{ dirText(row.dir) }}</div>
            <div class="muted m-line">
              {{ row.used_files }} / {{ row.max_files }} 个 · {{ fmtBytes(row.used_bytes) }} /
              {{ fmtBytes(row.max_total_bytes) }}
            </div>
            <div class="muted m-line">{{ fmtDateTime(row.expires_at) }} 过期</div>
          </div>
          <el-dropdown trigger="click" @command="(c) => rowCmd(c, row)">
            <el-button size="small" :icon="MoreFilled" />
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="revoke" :disabled="row.status === 'revoked'"
                  >吊销</el-dropdown-item
                >
                <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin-bottom: 14px;
}
.tb-refresh {
  margin-left: auto;
}
.total,
.tip {
  font-size: 0.85rem;
}
.muted {
  color: var(--muted);
}
.usage {
  display: flex;
  flex-direction: column;
  line-height: 1.5;
}
.usage .muted {
  font-size: 0.8rem;
}

/* 移动端卡片列表 */
.m-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 2px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.m-row:last-child {
  border-bottom: none;
}
.m-info {
  flex: 1;
  min-width: 0;
}
.m-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.m-label {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.m-line {
  font-size: 0.8rem;
  margin-top: 2px;
  word-break: break-all;
}
@media (max-width: 640px) {
  .tip {
    display: none;
  }
}
</style>
