<script setup>
import { ref, computed, onMounted } from 'vue'
import { Search, Delete, MoreFilled } from '@element-plus/icons-vue'
import { tagsApi } from '../../api'
import { useColWidths } from '../../utils/colWidths'
import { useIsMobile } from '../../composables/useIsMobile'

const { colW, onHeaderDrag } = useColWidths('dawnop_colw_tags')
const isMobile = useIsMobile()

// 移动卡片「更多」操作
function tagCmd(cmd, row) {
  if (cmd === 'rename') rename(row)
  else if (cmd === 'merge') openMerge(row)
  else if (cmd === 'delete') remove(row)
}

const items = ref([])
const loading = ref(true)
const fq = ref('')

// 合并对话框
const mergeVisible = ref(false)
const mergeSource = ref(null) // 被合并（将删除）的标签
const mergeTargetId = ref(null)
const merging = ref(false)

const filtered = computed(() => {
  const q = fq.value.trim().toLowerCase()
  if (!q) return items.value
  return items.value.filter(
    (t) => t.name.toLowerCase().includes(q) || t.slug.toLowerCase().includes(q),
  )
})

const mergeTargets = computed(() => items.value.filter((t) => t.id !== mergeSource.value?.id))

async function load() {
  loading.value = true
  try {
    const { data } = await tagsApi.listAll()
    items.value = data
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

async function rename(tag) {
  let value
  try {
    ;({ value } = await ElMessageBox.prompt(
      '新名称（slug 会随之重新生成）',
      `重命名「${tag.name}」`,
      {
        inputValue: tag.name,
        inputValidator: (v) => (v && v.trim() ? true : '标签名不能为空'),
        confirmButtonText: '重命名',
        cancelButtonText: '取消',
      },
    ))
  } catch (e) {
    return // 取消
  }
  if (value.trim() === tag.name) return
  try {
    await tagsApi.rename(tag.id, value.trim())
    ElMessage.success('已重命名')
    load()
  } catch (e) {
    /* 拦截器已提示（重名等 400） */
  }
}

function openMerge(tag) {
  mergeSource.value = tag
  mergeTargetId.value = null
  mergeVisible.value = true
}

async function doMerge() {
  if (!mergeTargetId.value) {
    ElMessage.warning('请选择目标标签')
    return
  }
  merging.value = true
  try {
    await tagsApi.merge(mergeSource.value.id, mergeTargetId.value)
    ElMessage.success('已合并')
    mergeVisible.value = false
    load()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    merging.value = false
  }
}

async function remove(tag) {
  try {
    await ElMessageBox.confirm(
      tag.count > 0
        ? `「${tag.name}」正被 ${tag.count} 篇文章使用，删除后这些文章将失去该标签。`
        : `确定删除标签「${tag.name}」？`,
      '删除标签',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch (e) {
    return // 取消
  }
  await tagsApi.remove(tag.id)
  ElMessage.success('已删除')
  load()
}

const orphanCount = computed(() => items.value.filter((t) => t.count === 0).length)

async function cleanup() {
  try {
    await ElMessageBox.confirm(
      `将删除 ${orphanCount.value} 个未被任何文章使用的标签。`,
      '清理未使用标签',
      { type: 'warning', confirmButtonText: '清理', cancelButtonText: '取消' },
    )
  } catch (e) {
    return // 取消
  }
  const { data } = await tagsApi.cleanup()
  ElMessage.success(`已清理 ${data.deleted} 个标签`)
  load()
}

onMounted(load)
</script>

<template>
  <div>
    <el-card shadow="never">
      <div class="toolbar">
        <el-input
          v-model="fq"
          class="search"
          placeholder="搜索标签名或 slug"
          :prefix-icon="Search"
          clearable
        />
        <span class="muted total">共 {{ items.length }} 个标签</span>
        <el-button v-if="orphanCount > 0" class="tb-clean" :icon="Delete" @click="cleanup">
          清理未使用（{{ orphanCount }}）
        </el-button>
      </div>

      <el-table
        v-if="!isMobile"
        v-loading="loading"
        :data="filtered"
        border
        empty-text="还没有标签，去文章里打上第一个吧"
        @header-dragend="onHeaderDrag"
      >
        <el-table-column label="名称" :width="colW['名称'] || 220" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="tag-name"><span class="hash">#</span>{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="标签页" :width="colW['标签页'] || 220" show-overflow-tooltip>
          <template #default="{ row }">
            <a :href="`/tag/${row.slug}`" target="_blank" rel="noopener" class="slug-link">
              /tag/{{ row.slug }}
            </a>
          </template>
        </el-table-column>
        <el-table-column prop="count" label="文章数" :width="colW.count || 120" sortable>
          <template #default="{ row }">
            <span :class="row.count === 0 ? 'orphan' : 'muted'">{{ row.count }}</span>
          </template>
        </el-table-column>
        <el-table-column />
        <el-table-column label="操作" :width="colW['操作'] || 200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="rename(row)">重命名</el-button>
            <el-button link type="primary" :disabled="items.length < 2" @click="openMerge(row)">
              合并
            </el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 移动端卡片列表 -->
      <div v-else v-loading="loading" class="m-list">
        <el-empty v-if="!filtered.length && !loading" description="还没有标签" :image-size="80" />
        <div v-for="row in filtered" :key="row.id" class="m-tag">
          <div class="m-tag-info">
            <span class="tag-name"><span class="hash">#</span>{{ row.name }}</span>
            <a :href="`/tag/${row.slug}`" target="_blank" rel="noopener" class="slug-link"
              >/tag/{{ row.slug }}</a
            >
            <span :class="row.count === 0 ? 'orphan' : 'muted'" class="m-count"
              >{{ row.count }} 篇</span
            >
          </div>
          <el-dropdown trigger="click" @command="(c) => tagCmd(c, row)">
            <el-button size="small" :icon="MoreFilled" />
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="rename">重命名</el-dropdown-item>
                <el-dropdown-item command="merge" :disabled="items.length < 2"
                  >合并</el-dropdown-item
                >
                <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>
    </el-card>

    <el-dialog v-model="mergeVisible" title="合并标签" :width="isMobile ? '92%' : '420px'">
      <p class="merge-hint">
        把 <b>#{{ mergeSource?.name }}</b
        >（{{ mergeSource?.count }} 篇）的文章并入下面选择的标签， 随后删除 #{{
          mergeSource?.name
        }}。
      </p>
      <el-select v-model="mergeTargetId" filterable placeholder="选择目标标签" class="w-full">
        <el-option
          v-for="t in mergeTargets"
          :key="t.id"
          :label="`${t.name}（${t.count} 篇）`"
          :value="t.id"
        />
      </el-select>
      <template #footer>
        <el-button @click="mergeVisible = false">取消</el-button>
        <el-button type="primary" :loading="merging" @click="doMerge">合并</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}
.tb-clean {
  margin-left: auto;
}
.search {
  width: 240px;
}
.total {
  font-size: 0.85rem;
}
.muted {
  color: var(--muted);
}
.orphan {
  color: var(--el-color-warning);
}
.tag-name .hash {
  color: var(--accent);
  margin-right: 1px;
}
.slug-link {
  font-size: 0.85rem;
  text-decoration: none;
}
.merge-hint {
  margin: 0 0 14px;
  line-height: 1.7;
  color: #1f2329;
}
.w-full {
  width: 100%;
}

/* 移动端卡片列表 */
.m-list {
  display: flex;
  flex-direction: column;
}
.m-tag {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 2px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.m-tag:last-child {
  border-bottom: none;
}
.m-tag-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
}
.m-tag-info .tag-name {
  font-weight: 600;
}
.m-count {
  font-size: 0.8rem;
}

@media (max-width: 768px) {
  .toolbar {
    flex-wrap: wrap;
  }
  .search {
    width: 100%;
  }
  .tb-clean {
    margin-left: 0;
    width: 100%;
  }
}
</style>
