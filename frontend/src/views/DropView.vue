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
  Document,
  Lock,
  ArrowRight,
  RefreshRight,
} from '@element-plus/icons-vue'
import ToolLayout from '../components/ToolLayout.vue'
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
const doneCount = computed(() => items.value.filter((it) => it.status === 'done').length)
const finishedCount = computed(() => items.value.length - reserved.value.files)
const queuedBytes = computed(() => items.value.reduce((sum, it) => sum + it.size, 0))
const progress = computed(() => {
  if (!items.value.length) return 0
  const total = items.value.reduce(
    (sum, it) => sum + (it.status === 'done' ? 100 : it.status === 'active' ? it.pct : 0),
    0,
  )
  return Math.round(total / items.value.length)
})
const hasFinished = computed(() => finishedCount.value > 0)
const availability = computed(
  () =>
    linkLost.value ||
    (expired.value ? '链接已过期' : exhausted.value ? '上传额度已用完' : '可以上传'),
)

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
  <ToolLayout name="drop" class="drop-page">
    <template v-if="phase === 'ready' && drop">
      <header v-if="drop.label" class="intro">
        <h1 class="tool-heading">{{ drop.label }}</h1>
      </header>

      <div class="workspace">
        <div class="upload-panel tool-panel">
          <div class="panel-heading">
            <h2>上传文件</h2>
            <span class="status-pill" :class="{ unavailable: !canAdd }">
              <i></i>{{ canAdd ? '可以上传' : '暂停接收' }}
            </span>
          </div>
          <p v-if="!canAdd" class="notice" role="status">{{ availability }}，无法继续添加文件。</p>
          <div
            class="zone"
            :class="{ over: dragging && canAdd, off: !canAdd }"
            role="button"
            :tabindex="canAdd ? 0 : -1"
            :aria-disabled="!canAdd"
            aria-label="选择要上传的文件，也可以拖拽文件到这里"
            @click="pick"
            @keydown.enter.prevent="pick"
            @keydown.space.prevent="pick"
            @dragenter.prevent="onDragEnter"
            @dragover.prevent
            @dragleave="onDragLeave"
            @drop.prevent="onDrop"
          >
            <div class="upload-art" aria-hidden="true">
              <span class="paper paper-back"
                ><el-icon><Document /></el-icon
              ></span>
              <span class="paper paper-front"
                ><el-icon><UploadFilled /></el-icon
              ></span>
            </div>
            <h3>{{ dragging && canAdd ? '松开，开始上传' : '拖拽文件到这里' }}</h3>
            <span class="choose-button tool-primary"
              >选择文件 <el-icon><ArrowRight /></el-icon
            ></span>
            <p class="zone-caption">单个文件不超过 {{ fmtBytes(drop.max_file_bytes) }}</p>
            <input ref="fileInput" type="file" multiple hidden @change="onPicked" />
          </div>
          <p class="upload-note">
            <el-icon><Lock /></el-icon> 仅接收方可见，同名文件自动改名。
          </p>

          <section v-if="items.length" class="list" aria-label="本次上传">
            <div class="list-head">
              <h2>
                本次上传 <span class="count">{{ items.length }}</span>
              </h2>
              <button v-if="hasFinished" class="text-button" @click="clearFinished">
                清除已结束
              </button>
            </div>
            <div class="queue-summary" role="status" aria-live="polite">
              <span>{{
                busy ? '上传中' : doneCount === items.length ? '上传完成' : '上传已结束'
              }}</span>
              <span
                >{{ doneCount }} / {{ items.length }} 个已完成 · {{ fmtBytes(queuedBytes) }}</span
              >
            </div>
            <el-progress v-if="busy" :percentage="progress" :stroke-width="3" :show-text="false" />
            <ul>
              <li v-for="it in items" :key="it.id" class="row" :class="it.status">
                <div class="file-icon">
                  <el-icon><Document /></el-icon>
                </div>
                <div class="file-detail">
                  <div class="row-top">
                    <span class="row-name" :title="it.name">{{ it.name }}</span>
                    <span class="row-size">{{ fmtBytes(it.size) }}</span>
                  </div>
                  <el-progress
                    v-if="it.status === 'active'"
                    :percentage="it.pct"
                    :stroke-width="3"
                    :show-text="false"
                    class="row-bar"
                  />
                  <p class="row-msg">
                    <template v-if="it.status === 'queued'">等待上传</template>
                    <template v-else-if="it.status === 'active'"
                      >{{ stageText[it.stage] || '上传中'
                      }}<template v-if="it.stage === 'upload'"> · {{ it.pct }}%</template></template
                    >
                    <template v-else-if="it.status === 'done'"
                      >已上传<template v-if="it.savedAs">
                        · 保存为 {{ it.savedAs }}</template
                      ></template
                    >
                    <template v-else-if="it.status === 'rejected'">未上传：{{ it.msg }}</template>
                    <template v-else>上传失败：{{ it.msg }}</template>
                  </p>
                </div>
                <el-icon class="row-status">
                  <CircleCheckFilled v-if="it.status === 'done'" />
                  <CircleCloseFilled
                    v-else-if="it.status === 'error' || it.status === 'rejected'"
                  />
                  <Loading v-else-if="it.status === 'active'" class="spin" />
                  <UploadFilled v-else />
                </el-icon>
              </li>
            </ul>
          </section>
        </div>

        <aside class="details-panel tool-panel" aria-label="上传链接信息">
          <div class="destination-icon">
            <el-icon><Folder /></el-icon>
          </div>
          <span class="eyebrow">接收文件夹</span>
          <h2 class="destination-name">{{ dirLabel }}</h2>
          <dl class="facts">
            <div class="quota-fact">
              <dt>还可上传</dt>
              <dd>
                <strong>{{ filesLeft }}</strong
                ><span> / {{ drop.max_files }} 个文件</span>
              </dd>
            </div>
            <div class="quota-fact">
              <dt>剩余空间</dt>
              <dd>
                <strong>{{ fmtBytes(bytesLeft) }}</strong>
              </dd>
              <p>总额度 {{ fmtBytes(drop.max_total_bytes) }}</p>
            </div>
            <div>
              <dt>单个文件上限</dt>
              <dd>{{ fmtBytes(drop.max_file_bytes) }}</dd>
            </div>
            <div>
              <dt>有效期至</dt>
              <dd>
                {{ fmtDateTime(drop.expires_at) }}<span class="sub">{{ leftText }}</span>
              </dd>
            </div>
          </dl>
        </aside>
      </div>
    </template>

    <section v-else class="state-card tool-state tool-panel" aria-live="polite">
      <template v-if="phase === 'loading'">
        <el-icon class="state-ico spin"><Loading /></el-icon>
        <h1>加载中</h1>
      </template>
      <template v-else-if="phase === 'missing'">
        <el-icon class="state-ico"><Folder /></el-icon>
        <h1>链接不完整</h1>
        <p>请使用接收方提供的完整上传链接。</p>
      </template>
      <template v-else-if="phase === 'dead'">
        <el-icon class="state-ico bad"><CircleCloseFilled /></el-icon>
        <h1>{{ deadTitle }}</h1>
        <p>{{ deadMsg }}</p>
      </template>
      <template v-else>
        <el-icon class="state-ico warn"><WarningFilled /></el-icon>
        <h1>加载失败</h1>
        <p>{{ loadErr }}</p>
        <button class="choose-button tool-primary" @click="load()">
          重试 <el-icon><RefreshRight /></el-icon>
        </button>
      </template>
    </section>
  </ToolLayout>
</template>

<style scoped>
.eyebrow {
  display: block;
  color: var(--accent);
  font-size: var(--el-font-size-extra-small);
  font-weight: 600;
  letter-spacing: 2px;
}
.intro {
  margin-bottom: var(--tool-heading-gap);
}
.workspace {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 270px;
  gap: 24px;
  align-items: start;
}
.upload-panel {
  padding: var(--tool-panel-padding);
}
.panel-heading,
.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.panel-heading {
  margin-bottom: 22px;
}
h2 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--fg);
}
.status-pill {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 9px;
  border-radius: 20px;
  background: var(--el-color-primary-light-9);
  color: var(--accent);
  font-size: var(--el-font-size-extra-small);
  white-space: nowrap;
}
.status-pill i {
  width: 5px;
  height: 5px;
  background: currentColor;
  border-radius: 50%;
}
.status-pill.unavailable {
  color: var(--el-color-warning-dark-2);
  background: var(--el-color-warning-light-9);
}
.notice {
  padding: 12px;
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning-dark-2);
  font-size: 12px;
  border-radius: 8px;
  overflow-wrap: anywhere;
}
.zone {
  border: 1px dashed var(--el-border-color);
  border-radius: var(--el-border-radius-base);
  padding: 36px 16px 25px;
  text-align: center;
  cursor: pointer;
  background: var(--bg);
  transition:
    border-color 0.18s,
    background 0.18s;
}
.zone:hover,
.zone.over {
  border-color: var(--accent);
  background: var(--el-color-primary-light-9);
}
.zone:focus-visible {
  outline: 3px solid var(--el-color-primary-light-7);
  outline-offset: 4px;
}
.zone.off {
  cursor: not-allowed;
  opacity: 0.55;
}
.upload-art {
  position: relative;
  height: 76px;
  width: 110px;
  margin: 0 auto 24px;
}
.paper {
  position: absolute;
  top: 0;
  display: grid;
  place-items: center;
  width: 58px;
  height: 72px;
  border-radius: 9px;
  font-size: 29px;
  border: 1px solid var(--el-color-primary-light-8);
}
.paper-back {
  left: 15px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary-light-5);
  transform: rotate(-13deg);
}
.paper-front {
  left: 44px;
  top: 8px;
  background: var(--bg);
  color: var(--accent);
  transform: rotate(9deg);
}
.zone h3 {
  margin: 0 0 5px;
  font-weight: 550;
  font-size: 18px;
  color: var(--fg);
}
.choose-button {
  margin-top: 22px;
}
.zone:hover .choose-button,
.zone.over .choose-button,
button.choose-button:hover {
  background: var(--accent-hover);
}
.zone-caption {
  color: var(--muted);
  font-size: var(--el-font-size-extra-small);
  margin: 16px 0 0;
}
.upload-note {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin: 15px 0 28px;
  color: var(--muted);
  font-size: var(--el-font-size-extra-small);
  line-height: 1.8;
}
.upload-note .el-icon {
  flex-shrink: 0;
}
.list {
  border-top: 1px solid var(--border);
  padding-top: 20px;
}
.list-head {
  margin-bottom: 16px;
}
.count {
  margin-left: 6px;
  color: var(--muted);
  font-size: var(--el-font-size-extra-small);
  font-weight: 400;
}
.text-button {
  border: 0;
  padding: 4px 0;
  background: none;
  color: var(--accent);
  font: inherit;
  font-size: var(--el-font-size-extra-small);
  cursor: pointer;
}
.queue-summary {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 6px;
  margin-bottom: 12px;
  color: var(--muted);
  font-size: var(--el-font-size-extra-small);
}
.list ul {
  margin: 0;
  padding: 0;
  list-style: none;
}
.row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 15px 0;
  border-bottom: 1px solid var(--border);
}
.row:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}
.file-icon {
  display: grid;
  place-items: center;
  flex: none;
  width: 34px;
  height: 42px;
  background: var(--el-color-primary-light-9);
  color: var(--accent);
  border-radius: 6px;
  font-size: 19px;
}
.file-detail {
  flex: 1;
  min-width: 0;
}
.row-top {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.row-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}
.row-size {
  flex: none;
  color: var(--muted);
  font-size: var(--el-font-size-extra-small);
  font-variant-numeric: tabular-nums;
}
.row-bar {
  margin-top: 7px;
}
.row-msg {
  margin: 3px 0 0;
  color: var(--muted);
  font-size: var(--el-font-size-extra-small);
  overflow-wrap: anywhere;
}
.row-status {
  flex: none;
  color: var(--el-text-color-placeholder);
}
.row.done .row-status,
.row.done .row-msg,
.row.active .row-status {
  color: var(--accent);
}
.row.error .row-status,
.row.rejected .row-status,
.row.error .row-msg,
.row.rejected .row-msg {
  color: var(--el-color-danger);
}
.details-panel {
  padding: 26px 24px;
}
.destination-icon {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  margin-bottom: 20px;
  background: var(--el-color-primary-light-9);
  border-radius: 10px;
  color: var(--accent);
  font-size: 24px;
}
.details-panel .eyebrow {
  color: var(--muted);
  font-size: var(--el-font-size-extra-small);
}
.destination-name {
  font-size: 20px;
  margin: 8px 0 20px;
  overflow-wrap: anywhere;
}
.facts {
  margin: 0;
}
.facts > div {
  padding: 15px 0;
  border-top: 1px solid var(--border);
}
.facts dt {
  color: var(--muted);
  font-size: var(--el-font-size-extra-small);
}
.facts dd {
  margin: 5px 0 0;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.quota-fact strong {
  font-size: 24px;
  font-weight: 550;
  letter-spacing: -0.6px;
}
.quota-fact dd span,
.quota-fact p {
  font-size: var(--el-font-size-extra-small);
  color: var(--muted);
}
.quota-fact p {
  margin: 3px 0 0;
}
.sub {
  display: block;
  margin-top: 4px;
  color: var(--muted);
  font-size: var(--el-font-size-extra-small);
}
.state-ico.bad {
  color: var(--el-color-danger);
}
.state-ico.warn {
  color: var(--el-color-warning);
}
.spin {
  animation: drop-spin 1s linear infinite;
}
@keyframes drop-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (max-width: 760px) {
  .workspace {
    grid-template-columns: minmax(0, 1fr);
    gap: 20px;
  }
  .details-panel {
    padding: 22px 20px;
  }
  .destination-icon {
    display: none;
  }
  .facts {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0 20px;
  }
}
@media (max-width: 380px) {
  .zone {
    padding-left: 10px;
    padding-right: 10px;
  }
  .row-top {
    flex-wrap: wrap;
  }
  .row-size {
    width: 100%;
  }
}
@media (prefers-reduced-motion: reduce) {
  .spin {
    animation: none;
  }
  .zone {
    transition: none;
  }
}
</style>
