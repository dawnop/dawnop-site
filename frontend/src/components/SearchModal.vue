<script setup>
import { ref, watch, nextTick, onBeforeUnmount } from 'vue'
import { fmtDate } from '../utils/format'
import { useRouter } from 'vue-router'
import { Search, Clock, Close } from '@element-plus/icons-vue'
import { searchApi } from '../api'
import { useIsMobile } from '../composables/useIsMobile'

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue'])

const router = useRouter()
const isMobile = useIsMobile()

const kw = ref('')
const items = ref([])
const total = ref(0)
const loading = ref(false)
const searched = ref(false) // 本次输入是否已发起过查询
const active = ref(0) // 键盘选中项
const recent = ref([])

const inputEl = ref(null)
const listEl = ref(null)

const MODAL_TOP_N = 6 // 面板只取前 N 条，「查看全部」跳整页
const RECENT_KEY = 'dawnop:recent-search'

function readMinutes(wc) {
  return Math.max(1, Math.round((wc || 0) / 300))
}

function loadRecent() {
  try {
    recent.value = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]').slice(0, 6)
  } catch (e) {
    recent.value = []
  }
}
function pushRecent(q) {
  const v = q.trim()
  if (!v) return
  const next = [v, ...recent.value.filter((x) => x !== v)].slice(0, 6)
  recent.value = next
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(next))
  } catch (e) {
    /* localStorage 不可用则忽略 */
  }
}
function clearRecent() {
  recent.value = []
  try {
    localStorage.removeItem(RECENT_KEY)
  } catch (e) {
    /* ignore */
  }
}

// —— 防抖即时搜索 ——
let timer = null
let reqId = 0
watch(kw, () => {
  clearTimeout(timer)
  const q = kw.value.trim()
  if (!q) {
    items.value = []
    total.value = 0
    searched.value = false
    loading.value = false
    return
  }
  loading.value = true
  timer = setTimeout(() => run(q), 200)
})

async function run(q) {
  const my = ++reqId
  try {
    const { data } = await searchApi.query(q, 1, MODAL_TOP_N)
    if (my !== reqId) return // 丢弃过期响应
    items.value = data.items
    total.value = data.total
  } catch (e) {
    if (my !== reqId) return
    items.value = []
    total.value = 0
  } finally {
    if (my === reqId) {
      loading.value = false
      searched.value = true
      active.value = 0
    }
  }
}

// —— 打开/关闭 ——
// 锁滚动时补偿滚动条宽度，避免页面因滚动条消失而横向偏移（与 Element 弹窗一致）
function lockScroll() {
  const sbw = window.innerWidth - document.documentElement.clientWidth
  document.body.style.overflow = 'hidden'
  if (sbw > 0) document.body.style.paddingRight = sbw + 'px'
}
function unlockScroll() {
  document.body.style.overflow = ''
  document.body.style.paddingRight = ''
}

function close() {
  emit('update:modelValue', false)
}
function openResult(a) {
  pushRecent(kw.value)
  close()
  router.push(`/article/${a.slug}`)
}
function viewAll() {
  const q = kw.value.trim()
  if (!q) return
  pushRecent(q)
  close()
  router.push({ name: 'search', query: { q } })
}
function useRecent(q) {
  kw.value = q
  nextTick(() => inputEl.value?.focus())
}

// —— 键盘导航 ——
function onKeydown(e) {
  if (e.key === 'Escape') {
    e.preventDefault()
    close()
  } else if (e.key === 'ArrowDown') {
    e.preventDefault()
    if (items.value.length) active.value = Math.min(active.value + 1, items.value.length - 1)
    scrollActive()
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    if (items.value.length) active.value = Math.max(active.value - 1, 0)
    scrollActive()
  } else if (e.key === 'Enter') {
    e.preventDefault()
    if (items.value.length && items.value[active.value]) openResult(items.value[active.value])
    else if (kw.value.trim()) viewAll()
  }
}
function scrollActive() {
  nextTick(() => {
    listEl.value?.querySelectorAll('.s-item')[active.value]?.scrollIntoView({ block: 'nearest' })
  })
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      kw.value = ''
      items.value = []
      total.value = 0
      searched.value = false
      active.value = 0
      loadRecent()
      lockScroll()
      nextTick(() => inputEl.value?.focus())
    } else {
      unlockScroll()
    }
  }
)

onBeforeUnmount(() => {
  unlockScroll()
})
</script>

<template>
  <Teleport to="body">
    <Transition name="s-fade">
      <div v-if="modelValue" class="s-overlay" @mousedown.self="close">
        <div class="s-panel" role="dialog" aria-modal="true" @keydown="onKeydown">
          <!-- 输入行 -->
          <div class="s-head">
            <el-icon class="s-head-icon"><Search /></el-icon>
            <input
              ref="inputEl"
              v-model="kw"
              class="s-input"
              type="text"
              placeholder="搜索文章标题、摘要、正文…"
              autocomplete="off"
              spellcheck="false"
            />
            <kbd v-if="!isMobile" class="s-esc" @click="close">Esc</kbd>
            <button v-else class="s-close" type="button" aria-label="关闭" @click="close">
              <el-icon><Close /></el-icon>
            </button>
          </div>

          <!-- 结果 / 最近搜索 / 空态 -->
          <div ref="listEl" class="s-body">
            <!-- 未输入：最近搜索 -->
            <template v-if="!kw.trim()">
              <div v-if="recent.length" class="s-section">
                <div class="s-section-head">
                  <span>最近搜索</span>
                  <button class="s-clear" @click="clearRecent">清除</button>
                </div>
                <button
                  v-for="q in recent"
                  :key="q"
                  class="s-recent"
                  @click="useRecent(q)"
                >
                  <el-icon><Clock /></el-icon>
                  <span>{{ q }}</span>
                </button>
              </div>
              <div v-else class="s-hint">
                <el-icon class="s-hint-icon"><Search /></el-icon>
                <p>输入关键词开始搜索</p>
                <p class="s-hint-sub">支持中文、拼音、拼音首字母</p>
              </div>
            </template>

            <!-- 加载中 -->
            <div v-else-if="loading && !searched" class="s-loading">
              <el-icon class="is-loading"><Search /></el-icon>
              <span>搜索中…</span>
            </div>

            <!-- 无结果 -->
            <div v-else-if="searched && items.length === 0" class="s-hint">
              <p>没有找到「{{ kw.trim() }}」相关的文章</p>
              <p class="s-hint-sub">换个关键词试试</p>
            </div>

            <!-- 结果列表 -->
            <button
              v-for="(a, i) in items"
              :key="a.id"
              class="s-item"
              :class="{ active: i === active }"
              @click="openResult(a)"
              @mousemove="active = i"
            >
              <span class="s-title" v-html="a.title_html"></span>
              <span v-if="a.excerpt_html" class="s-excerpt" v-html="a.excerpt_html"></span>
              <span class="s-meta">
                <span>{{ fmtDate(a.created_at) }}</span>
                <span class="s-dot">·</span>
                <span>约 {{ readMinutes(a.word_count) }} 分钟</span>
                <template v-if="a.tags && a.tags.length">
                  <span class="s-dot">·</span>
                  <span v-for="t in a.tags" :key="t.slug" class="s-tag">#{{ t.name }}</span>
                </template>
              </span>
            </button>
          </div>

          <!-- 底栏（移动端隐藏键盘提示；无「查看全部」时整条不显示） -->
          <div v-if="!isMobile || (items.length && total > items.length)" class="s-foot">
            <div v-if="!isMobile" class="s-keys">
              <span><kbd>↑</kbd><kbd>↓</kbd> 选择</span>
              <span><kbd>↵</kbd> 打开</span>
              <span><kbd>esc</kbd> 关闭</span>
            </div>
            <button
              v-if="items.length && total > items.length"
              class="s-viewall"
              @click="viewAll"
            >
              查看全部 {{ total }} 条 →
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.s-overlay {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(15, 18, 25, 0.42);
  backdrop-filter: blur(2px);
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 12vh 16px 16px;
}
.s-panel {
  width: 100%;
  max-width: 600px;
  max-height: 76vh;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 14px;
  box-shadow: 0 16px 48px rgba(15, 18, 25, 0.22), 0 2px 8px rgba(15, 18, 25, 0.08);
  overflow: hidden;
}

/* 输入行 */
.s-head {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 0 14px;
  height: 56px;
  border-bottom: 1px solid #f0f1f3;
  flex: none;
}
.s-head-icon {
  font-size: 19px;
  color: #9aa0a6;
}
.s-input {
  flex: 1;
  border: none;
  outline: none;
  font-size: 1.05rem;
  color: #1a1a1a;
  background: transparent;
}
.s-input::placeholder {
  color: #b3b8bf;
}
.s-esc {
  font-size: 0.72rem;
  color: #8a9099;
  border: 1px solid #e3e5e8;
  border-radius: 5px;
  padding: 2px 6px;
  cursor: pointer;
  background: #fafbfc;
  line-height: 1.2;
}
.s-esc:hover {
  color: #5a5a5a;
  border-color: #d0d3d7;
}

/* 结果区 */
.s-body {
  overflow-y: auto;
  padding: 8px;
  flex: 1;
  min-height: 0;
}

.s-section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 8px;
  font-size: 0.76rem;
  color: #9aa0a6;
  letter-spacing: 0.3px;
}
.s-clear {
  border: none;
  background: none;
  color: #9aa0a6;
  cursor: pointer;
  font-size: 0.76rem;
}
.s-clear:hover {
  color: var(--accent);
}
.s-recent {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  border: none;
  background: none;
  padding: 9px 10px;
  border-radius: 8px;
  cursor: pointer;
  color: #3c4149;
  font-size: 0.92rem;
  text-align: left;
}
.s-recent:hover {
  background: #f5f7fa;
}
.s-recent .el-icon {
  color: #b3b8bf;
  font-size: 15px;
}

/* 结果项 */
.s-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  border: none;
  background: none;
  text-align: left;
  padding: 11px 12px;
  border-radius: 9px;
  cursor: pointer;
}
.s-item.active {
  background: #f0f6ff;
}
.s-title {
  font-size: 0.98rem;
  font-weight: 600;
  color: #1a1a1a;
  line-height: 1.4;
}
.s-item.active .s-title {
  color: var(--accent);
}
.s-excerpt {
  font-size: 0.85rem;
  color: #6b7178;
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.s-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 0.78rem;
  color: #9aa0a6;
  margin-top: 2px;
}
.s-dot {
  color: #d0d3d7;
}
.s-tag {
  color: var(--accent);
  opacity: 0.9;
}

/* 空态 / 加载 */
.s-hint {
  text-align: center;
  padding: 44px 16px;
  color: #7a8089;
}
.s-hint p {
  margin: 0;
  font-size: 0.95rem;
}
.s-hint-icon {
  font-size: 30px;
  color: #d4d8dd;
  margin-bottom: 10px;
}
.s-hint-sub {
  margin-top: 6px !important;
  font-size: 0.82rem !important;
  color: #b3b8bf;
}
.s-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  padding: 40px 16px;
  color: #9aa0a6;
  font-size: 0.9rem;
}

/* 底栏 */
.s-foot {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 9px 14px;
  border-top: 1px solid #f0f1f3;
  background: #fafbfc;
}
.s-keys {
  display: flex;
  gap: 14px;
  font-size: 0.76rem;
  color: #9aa0a6;
}
.s-keys span {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.s-foot kbd {
  font-family: inherit;
  font-size: 0.72rem;
  color: #8a9099;
  border: 1px solid #e3e5e8;
  border-bottom-width: 2px;
  border-radius: 4px;
  padding: 0 4px;
  min-width: 16px;
  height: 17px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #fff;
}
.s-viewall {
  border: none;
  background: none;
  color: var(--accent);
  cursor: pointer;
  font-size: 0.82rem;
  font-weight: 500;
}
.s-viewall:hover {
  text-decoration: underline;
}

/* 命中高亮 */
.s-body :deep(mark) {
  background: #fff1b8;
  color: inherit;
  padding: 0 1px;
  border-radius: 2px;
}

/* 过渡 */
.s-fade-enter-active,
.s-fade-leave-active {
  transition: opacity 0.16s ease;
}
.s-fade-enter-active .s-panel,
.s-fade-leave-active .s-panel {
  transition: transform 0.18s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.18s ease;
}
.s-fade-enter-from,
.s-fade-leave-to {
  opacity: 0;
}
.s-fade-enter-from .s-panel,
.s-fade-leave-to .s-panel {
  transform: translateY(-8px) scale(0.98);
  opacity: 0;
}

/* 关闭按钮（移动端全屏时的明确关闭入口，44px 触控目标） */
.s-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  margin-right: -6px;
  border: none;
  background: none;
  color: #8a9099;
  cursor: pointer;
  font-size: 20px;
}
.s-close:active {
  color: #5a5a5a;
}

/* 移动端：全屏（dvh 适配 iOS 键盘弹起，避免底栏被顶出视口） */
@media (max-width: 560px) {
  .s-overlay {
    padding: 0;
  }
  .s-panel {
    max-width: 100%;
    max-height: 100vh;
    max-height: 100dvh;
    height: 100vh;
    height: 100dvh;
    border-radius: 0;
  }
  .s-head {
    height: 54px;
    padding: 0 8px 0 14px;
  }
  .s-item {
    padding: 13px 12px;
  }
  .s-recent {
    padding: 12px 10px;
  }
  .s-foot {
    justify-content: center;
    padding: 12px 14px;
  }
  .s-viewall {
    font-size: 0.9rem;
  }
}
</style>
