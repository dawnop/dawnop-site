<script setup>
// 公开上传链接页 /drop#<token>：匿名访客往管理员指定的目录里上传文件。
// token 放在 URL 片段里（不进 nginx 日志、不随 Referer 外发），只经 dropApi 的 X-Drop-Token 头发给后端。
// 本页只展示链接自身的信息与本次上传的结果，不展示目录里任何已有文件（后端也不提供）。
// 额度在前端先预检一遍，只为给访客早点反馈；真正的判定在服务端，前端放行的照样可能被拒。
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import {
  UploadFilled,
  CircleCheckFilled,
  CircleCloseFilled,
  WarningFilled,
  Loading,
  Folder,
} from '@element-plus/icons-vue'
import * as dropApi from '../api/dropApi'
import { fmtBytes, fmtDateTime, fmtDuration, toMs } from '../utils/format'

// 同时上传的文件数：访客侧不读后台全局设置（那要管理员登录），固定一个保守值
const CONCURRENCY = 2

// ---------- Referrer：本页发出的一切请求都不带来源 ----------
// 片段本就不进 Referer，这里再加一道，防止页面地址以别的形式外泄。离开本页时恢复原状。
const refMeta = (() => {
  let el = document.querySelector('meta[name="referrer"]')
  const created = !el
  const prev = el?.getAttribute('content')
  if (!el) {
    el = document.createElement('meta')
    el.setAttribute('name', 'referrer')
    document.head.appendChild(el)
  }
  el.setAttribute('content', 'no-referrer')
  return { el, created, prev }
})()

// ---------- 链接状态 ----------
// phase: loading | missing（地址里没有 token）| dead（404/410，链接不可用）| error（网络等，可重试）| ready
const phase = ref('loading')
const deadTitle = ref('')
const deadMsg = ref('')
const loadErr = ref('')
const drop = ref(null)
let token = ''

function readToken() {
  const raw = location.hash.replace(/^#/, '')
  try {
    return decodeURIComponent(raw).trim()
  } catch {
    return raw.trim() // 片段里有残缺的 % 转义：原样交给后端判定
  }
}

function markDead(err) {
  const status = err?.response?.status
  deadTitle.value = status === 404 ? '上传链接无效' : '上传链接已失效'
  deadMsg.value =
    status === 404 ? '链接不存在或已被删除，请向分享者确认链接是否完整。' : dropApi.errorText(err)
}

async function load(silent = false) {
  token = readToken()
  if (!token) {
    phase.value = 'missing'
    return
  }
  if (!silent) phase.value = 'loading'
  try {
    drop.value = await dropApi.info(token)
    phase.value = 'ready'
  } catch (err) {
    const status = err?.response?.status
    if ((status === 404 || status === 410) && silent) {
      // 传完后的静默刷新：最后一个文件用尽额度时服务端就回 410，这时保留本次结果，只给提示
      linkLost.value = dropApi.errorText(err)
    } else if (status === 404 || status === 410) {
      markDead(err)
      phase.value = 'dead'
    } else if (!silent) {
      loadErr.value = dropApi.errorText(err)
      phase.value = 'error'
    }
  }
}

// ---------- 时间与额度 ----------
const now = ref(Date.now())
let clock = null
const expiresMs = computed(() => toMs(drop.value?.expires_at))
const expired = computed(() => Number.isFinite(expiresMs.value) && now.value >= expiresMs.value)
const leftText = computed(() => {
  if (!Number.isFinite(expiresMs.value)) return ''
  const sec = Math.floor((expiresMs.value - now.value) / 1000)
  if (sec <= 0) return '已过期'
  return sec < 60 ? '不到 1 分钟后过期' : `${fmtDuration(sec)}后过期`
})

const items = ref([])
let seq = 0
// 排队中和上传中的文件先占住额度，预检后来者时一起算
const reserved = computed(() => {
  let files = 0
  let bytes = 0
  for (const it of items.value) {
    if (it.status === 'queued' || it.status === 'active') {
      files++
      bytes += it.size
    }
  }
  return { files, bytes }
})
const filesLeft = computed(() =>
  drop.value ? Math.max(0, drop.value.max_files - drop.value.used_files) : 0,
)
const bytesLeft = computed(() =>
  drop.value ? Math.max(0, drop.value.max_total_bytes - drop.value.used_bytes) : 0,
)
const exhausted = computed(() => drop.value && (filesLeft.value <= 0 || bytesLeft.value <= 0))
// 链接中途失效（上传时收到 404/410）：保留已有结果，只禁止继续添加
const linkLost = ref('')
const canAdd = computed(
  () => phase.value === 'ready' && !expired.value && !exhausted.value && !linkLost.value,
)
const busy = computed(() => reserved.value.files > 0)
const dirLabel = computed(() => drop.value?.dir_name || '根目录')

// 预检：单文件上限 → 文件数 → 剩余总字节，顺序与服务端一致；不通过的直接标为「未上传」
function precheck(file) {
  const d = drop.value
  if (file.size > d.max_file_bytes) return `超过单个文件上限 ${fmtBytes(d.max_file_bytes)}`
  if (filesLeft.value - reserved.value.files <= 0) return '已达到可上传的文件数上限'
  if (file.size > bytesLeft.value - reserved.value.bytes) {
    return `超过剩余总额度 ${fmtBytes(Math.max(0, bytesLeft.value - reserved.value.bytes))}`
  }
  return ''
}

function addFiles(list) {
  if (!canAdd.value) return
  for (const file of list) {
    const reason = precheck(file)
    items.value.push(
      reactive({
        id: ++seq,
        file,
        name: file.name,
        size: file.size,
        status: reason ? 'rejected' : 'queued',
        stage: '',
        pct: 0,
        msg: reason,
        savedAs: '',
      }),
    )
  }
  pump()
}

// ---------- 上传队列 ----------
let running = 0
function pump() {
  while (running < CONCURRENCY) {
    const it = items.value.find((x) => x.status === 'queued')
    if (!it) break
    running++
    run(it).finally(() => {
      running--
      if (!items.value.some((x) => x.status === 'queued' || x.status === 'active') && !running) {
        // 一批传完：静默刷新一次额度，以服务端为准（并发上传、别人同时在用同一链接都可能让本地数偏）
        if (!linkLost.value) load(true)
      } else pump()
    })
  }
}

async function run(it) {
  it.status = 'active'
  try {
    const res = await dropApi.uploadFile(
      token,
      it.file,
      (p) => (it.pct = Math.round(p * 100)),
      (s) => (it.stage = s),
    )
    it.pct = 100
    it.status = 'done'
    it.savedAs = res?.name && res.name !== it.name ? res.name : ''
    if (drop.value) {
      drop.value.used_files += 1
      drop.value.used_bytes += Number(res?.size ?? it.size) || 0
    }
  } catch (err) {
    it.status = 'error'
    it.msg = dropApi.errorText(err)
    const status = err?.response?.status
    if (status === 404 || status === 410) {
      linkLost.value = it.msg
      for (const x of items.value) {
        if (x.status === 'queued') {
          x.status = 'error'
          x.msg = '链接已失效，未上传'
        }
      }
    }
  } finally {
    it.file = null // 传完就放掉 File 引用
  }
}

const stageText = { token: '准备中', upload: '上传中', register: '登记中' }

// ---------- 选择 / 拖拽 ----------
const fileInput = ref(null)
const dragging = ref(false)
let dragDepth = 0
function pick() {
  if (canAdd.value) fileInput.value?.click()
}
function onPicked(ev) {
  const files = [...(ev.target.files || [])]
  ev.target.value = ''
  addFiles(files)
}
function onDragEnter() {
  dragDepth++
  dragging.value = true
}
function onDragLeave() {
  dragDepth = Math.max(0, dragDepth - 1)
  if (!dragDepth) dragging.value = false
}
function onDrop(ev) {
  dragDepth = 0
  dragging.value = false
  // 只收文件，拖进来的文件夹（size 0 且无类型的条目）浏览器给不出内容，交给服务端拒绝也行，这里先滤掉
  const files = [...(ev.dataTransfer?.files || [])].filter((f) => f.size > 0 || f.type)
  addFiles(files)
}

function clearFinished() {
  items.value = items.value.filter((x) => x.status === 'queued' || x.status === 'active')
}

// 上传中离开页面要确认
function onBeforeUnload(e) {
  if (busy.value) {
    e.preventDefault()
    e.returnValue = ''
  }
}

function onHashChange() {
  items.value = items.value.filter((x) => x.status === 'active')
  linkLost.value = ''
  load()
}

onMounted(() => {
  load()
  clock = setInterval(() => (now.value = Date.now()), 30_000)
  window.addEventListener('beforeunload', onBeforeUnload)
  window.addEventListener('hashchange', onHashChange)
})
onUnmounted(() => {
  clearInterval(clock)
  window.removeEventListener('beforeunload', onBeforeUnload)
  window.removeEventListener('hashchange', onHashChange)
  if (refMeta.created) refMeta.el.remove()
  else if (refMeta.prev === null) refMeta.el.removeAttribute('content')
  else refMeta.el.setAttribute('content', refMeta.prev)
})
</script>

<template>
  <div class="drop-page">
    <main class="drop-card">
      <!-- 加载中 -->
      <div v-if="phase === 'loading'" class="state">
        <el-icon class="state-ico spin"><Loading /></el-icon>
        <p class="state-text">正在读取上传链接…</p>
      </div>

      <!-- 地址里没有 token -->
      <div v-else-if="phase === 'missing'" class="state">
        <el-icon class="state-ico warn"><WarningFilled /></el-icon>
        <h1 class="state-title">链接不完整</h1>
        <p class="state-text">
          请使用分享者发给你的完整上传链接打开本页，链接中 <code>#</code> 后面的部分不能缺少。
        </p>
      </div>

      <!-- 404 / 410 -->
      <div v-else-if="phase === 'dead'" class="state">
        <el-icon class="state-ico bad"><CircleCloseFilled /></el-icon>
        <h1 class="state-title">{{ deadTitle }}</h1>
        <p class="state-text">{{ deadMsg }}</p>
      </div>

      <!-- 网络等其他错误 -->
      <div v-else-if="phase === 'error'" class="state">
        <el-icon class="state-ico warn"><WarningFilled /></el-icon>
        <h1 class="state-title">暂时无法读取上传链接</h1>
        <p class="state-text">{{ loadErr }}</p>
        <el-button @click="load()">重试</el-button>
      </div>

      <!-- 正常 -->
      <template v-else-if="drop">
        <header class="head">
          <h1 class="title">{{ drop.label || '上传文件' }}</h1>
          <p class="dest">
            <el-icon><Folder /></el-icon>
            <span
              >文件将上传到 <b>{{ dirLabel }}</b></span
            >
          </p>
        </header>

        <dl class="facts">
          <div>
            <dt>剩余文件数</dt>
            <dd>{{ filesLeft }} / {{ drop.max_files }}</dd>
          </div>
          <div>
            <dt>剩余总额度</dt>
            <dd>{{ fmtBytes(bytesLeft) }} / {{ fmtBytes(drop.max_total_bytes) }}</dd>
          </div>
          <div>
            <dt>单个文件上限</dt>
            <dd>{{ fmtBytes(drop.max_file_bytes) }}</dd>
          </div>
          <div>
            <dt>有效期至</dt>
            <dd :title="leftText">
              {{ fmtDateTime(drop.expires_at) }}
              <span class="sub">{{ leftText }}</span>
            </dd>
          </div>
        </dl>

        <el-alert
          v-if="linkLost"
          type="error"
          :title="`上传链接已不可用：${linkLost}`"
          :closable="false"
          show-icon
          class="notice"
        />
        <el-alert
          v-else-if="expired"
          type="warning"
          title="上传链接已过期，无法继续上传。"
          :closable="false"
          show-icon
          class="notice"
        />
        <el-alert
          v-else-if="exhausted"
          type="warning"
          title="上传额度已用完，无法继续上传。"
          :closable="false"
          show-icon
          class="notice"
        />

        <div
          class="zone"
          :class="{ over: dragging && canAdd, off: !canAdd }"
          role="button"
          :tabindex="canAdd ? 0 : -1"
          :aria-disabled="!canAdd"
          @click="pick"
          @keydown.enter.prevent="pick"
          @keydown.space.prevent="pick"
          @dragenter.prevent="onDragEnter"
          @dragover.prevent
          @dragleave="onDragLeave"
          @drop.prevent="onDrop"
        >
          <el-icon class="zone-ico"><UploadFilled /></el-icon>
          <p class="zone-main">拖拽文件到这里，或<span class="zone-link">点击选择文件</span></p>
          <p class="zone-sub">可一次选择多个文件；同名文件不会覆盖，会自动改名保存</p>
          <input ref="fileInput" type="file" multiple hidden @change="onPicked" />
        </div>

        <section v-if="items.length" class="list">
          <div class="list-head">
            <span>本次上传</span>
            <el-button v-if="!busy" link type="primary" @click="clearFinished">清空列表</el-button>
          </div>
          <ul>
            <li v-for="it in items" :key="it.id" class="row" :class="it.status">
              <div class="row-top">
                <el-icon class="row-ico">
                  <CircleCheckFilled v-if="it.status === 'done'" />
                  <CircleCloseFilled
                    v-else-if="it.status === 'error' || it.status === 'rejected'"
                  />
                  <Loading v-else-if="it.status === 'active'" class="spin" />
                  <UploadFilled v-else />
                </el-icon>
                <span class="row-name" :title="it.name">{{ it.name }}</span>
                <span class="row-size">{{ fmtBytes(it.size) }}</span>
              </div>
              <el-progress
                v-if="it.status === 'active'"
                :percentage="it.pct"
                :stroke-width="4"
                :show-text="false"
                class="row-bar"
              />
              <p class="row-msg">
                <template v-if="it.status === 'queued'">等待上传</template>
                <template v-else-if="it.status === 'active'"
                  >{{ stageText[it.stage] || '上传中' }}
                  <template v-if="it.stage === 'upload'">{{ it.pct }}%</template></template
                >
                <template v-else-if="it.status === 'done'"
                  >已上传<template v-if="it.savedAs">，保存为 {{ it.savedAs }}</template></template
                >
                <template v-else-if="it.status === 'rejected'">未上传：{{ it.msg }}</template>
                <template v-else>上传失败：{{ it.msg }}</template>
              </p>
            </li>
          </ul>
        </section>
      </template>
    </main>
    <p class="foot">上传后的文件仅分享者可见，本页不会显示目录中的已有文件。</p>
  </div>
</template>

<style scoped>
.drop-page {
  min-height: 100vh;
  min-height: 100dvh;
  background: #f5f6f8;
  padding: 48px 16px 32px;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.drop-card {
  width: 100%;
  max-width: 560px;
  background: #fff;
  border: 1px solid #ebedf0;
  border-radius: 12px;
  padding: 28px;
}
.foot {
  max-width: 560px;
  margin: 16px 0 0;
  font-size: 0.8rem;
  color: #8c8c8c;
  text-align: center;
}

/* 状态页（加载 / 无 token / 失效 / 出错） */
.state {
  text-align: center;
  padding: 24px 0 8px;
}
.state-ico {
  font-size: 44px;
  color: #8c8c8c;
}
.state-ico.warn {
  color: var(--el-color-warning);
}
.state-ico.bad {
  color: var(--el-color-danger);
}
.state-title {
  font-size: 1.2rem;
  margin: 12px 0 6px;
}
.state-text {
  color: #57606a;
  margin: 8px 0 16px;
  line-height: 1.7;
  word-break: break-word;
}
.state-text code {
  background: #f0f1f3;
  padding: 0 4px;
  border-radius: 4px;
}

.head {
  margin-bottom: 18px;
}
.title {
  font-size: 1.35rem;
  margin: 0 0 6px;
  word-break: break-word;
}
.dest {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  color: #57606a;
  font-size: 0.92rem;
  word-break: break-all;
}

.facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 16px;
  margin: 0 0 18px;
  padding: 14px 16px;
  background: #fafbfc;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
}
.facts dt {
  font-size: 0.78rem;
  color: #8c8c8c;
}
.facts dd {
  margin: 2px 0 0;
  font-size: 0.95rem;
  font-variant-numeric: tabular-nums;
}
.facts .sub {
  display: block;
  font-size: 0.78rem;
  color: #8c8c8c;
}

.notice {
  margin-bottom: 14px;
}

.zone {
  border: 1.5px dashed #d9d9d9;
  border-radius: 10px;
  padding: 28px 16px;
  text-align: center;
  cursor: pointer;
  transition:
    border-color 0.15s,
    background 0.15s;
  outline: none;
}
.zone:hover,
.zone:focus-visible,
.zone.over {
  border-color: var(--accent);
  background: #f5f9ff;
}
.zone.off {
  cursor: not-allowed;
  opacity: 0.55;
  border-color: #d9d9d9;
  background: transparent;
}
.zone-ico {
  font-size: 40px;
  color: var(--accent);
}
.zone-main {
  margin: 8px 0 4px;
}
.zone-link {
  color: var(--accent);
  margin-left: 2px;
}
.zone-sub {
  margin: 0;
  font-size: 0.8rem;
  color: #8c8c8c;
}

.list {
  margin-top: 20px;
}
.list-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.85rem;
  color: #8c8c8c;
  margin-bottom: 6px;
}
.list ul {
  list-style: none;
  margin: 0;
  padding: 0;
}
.row {
  padding: 10px 0;
  border-top: 1px solid #f0f0f0;
}
.row-top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.row-ico {
  flex: none;
  color: #8c8c8c;
}
.row.done .row-ico {
  color: var(--el-color-success);
}
.row.error .row-ico,
.row.rejected .row-ico {
  color: var(--el-color-danger);
}
.row.active .row-ico {
  color: var(--accent);
}
.row-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row-size {
  flex: none;
  font-size: 0.8rem;
  color: #8c8c8c;
  font-variant-numeric: tabular-nums;
}
.row-bar {
  margin: 6px 0 0 24px;
}
.row-msg {
  margin: 2px 0 0 24px;
  font-size: 0.8rem;
  color: #8c8c8c;
  word-break: break-word;
}
.row.error .row-msg,
.row.rejected .row-msg {
  color: var(--el-color-danger);
}

.spin {
  animation: drop-spin 1s linear infinite;
}
@keyframes drop-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 640px) {
  .drop-page {
    padding-top: 16px;
  }
  .drop-card {
    padding: 20px 16px;
  }
  .zone {
    padding: 24px 12px;
  }
}
</style>
