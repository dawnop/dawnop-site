<!-- viz-name: Radix Select 逐轮缩桶 -->
<script setup>
import { ref, reactive, computed, onBeforeUnmount } from 'vue'

// 6-bit 整数（0..63），每轮看 2 个 bit（4 个 bin），从高位到低位逐轮锁定第 k 大所在的 bin。
// 递归动画：点「下一轮」→ pivot 柱扩展铺满窗口（钻进该 bin 内部）→ 再裂成 4 根新子柱。
const N = 24
const BITS = 6
const D = 2 // 每轮 bit 数 → 4 个 bin

function distinctVals(n) {
  const pool = [...Array(1 << BITS).keys()]
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[pool[i], pool[j]] = [pool[j], pool[i]]
  }
  return pool.slice(0, n)
}

const vals = ref(distinctVals(N))
const k = ref(6)

const st = ref({
  active: [],
  kCur: k.value,
  shift: BITS - D,
  settled: 0,
  round: 0,
  finished: false,
})
const binOf = (idx, shift) => (vals.value[idx] >> shift) & 3

const view = computed(() => {
  const s = st.value
  const bins = [[], [], [], []]
  for (const idx of s.active) bins[binOf(idx, s.shift)].push(idx)
  const counts = bins.map((b) => b.length)
  let cum = 0,
    pivot = 0,
    above = 0
  for (let b = 3; b >= 0; b--) {
    if (cum + counts[b] >= s.kCur) {
      pivot = b
      above = cum
      break
    }
    cum += counts[b]
  }
  return { bins, counts, pivot, above }
})

const statusOf = (b) => {
  const v = view.value
  if (st.value.finished) return 'top'
  return b > v.pivot ? 'top' : b === v.pivot ? 'pivot' : 'excluded'
}

const activeSorted = computed(() =>
  st.value.active
    .map((idx) => ({ idx, v: vals.value[idx], b: binOf(idx, st.value.shift) }))
    .sort((a, b) => b.v - a.v),
)

// ---- 直方图布局 ----
const barW = 96,
  barGap = 28,
  baseY = 150,
  maxBarH = 96
const groupX = computed(() => (560 - (4 * barW + 3 * barGap)) / 2)
const fullW = 4 * barW + 3 * barGap
const barX = (b) => groupX.value + (3 - b) * (barW + barGap) // bin3(最大)在最左
const maxCount = computed(() => Math.max(1, ...view.value.counts))
const barH = (c) => Math.round((c / maxCount.value) * maxBarH)
const fillFor = (status) =>
  status === 'top' ? '#e6f2ec' : status === 'pivot' ? 'rgba(22,119,255,0.10)' : '#eef1f6'
const strokeFor = (status) =>
  status === 'top' ? '#5fb189' : status === 'pivot' ? '#1677ff' : '#c4ccd8'

// 每根柱的渲染态（rAF 期间被动画更新；静止时与 view 同步）
const disp = reactive(
  [0, 1, 2, 3].map(() => ({ x: 0, w: barW, h: 0, fill: '#eef1f6', stroke: '#c4ccd8', op: 1 })),
)
function syncDisp() {
  const v = view.value
  for (let b = 0; b < 4; b++)
    disp[b] = {
      x: barX(b),
      w: barW,
      h: barH(v.counts[b]),
      fill: fillFor(statusOf(b)),
      stroke: strokeFor(statusOf(b)),
      op: 1,
    }
}

const transitioning = ref(false)
let raf = null
function animateTo(targets, dur, done) {
  const starts = disp.map((d) => ({ ...d }))
  const t0 = performance.now()
  const tick = (now) => {
    const t = Math.min(1, (now - t0) / dur),
      e = t * t * (3 - 2 * t)
    for (let b = 0; b < 4; b++) {
      const s = starts[b],
        g = targets[b]
      disp[b] = {
        x: s.x + (g.x - s.x) * e,
        w: s.w + (g.w - s.w) * e,
        h: s.h + (g.h - s.h) * e,
        op: s.op + (g.op - s.op) * e,
        fill: g.fill,
        stroke: g.stroke,
      }
    }
    if (t < 1) raf = requestAnimationFrame(tick)
    else done && done()
  }
  raf = requestAnimationFrame(tick)
}

function next() {
  const s = st.value
  if (s.finished || transitioning.value) return
  const v = view.value
  const pivot = v.pivot
  const fx = groupX.value
  transitioning.value = true

  // 阶段一：zoom —— pivot 柱扩展铺满窗口，其余淡出（钻进 pivot bin 内部）
  const zoomT = [0, 1, 2, 3].map((b) =>
    b === pivot
      ? { x: fx, w: fullW, h: maxBarH, fill: fillFor('pivot'), stroke: strokeFor('pivot'), op: 1 }
      : {
          x: disp[b].x,
          w: disp[b].w,
          h: disp[b].h,
          fill: disp[b].fill,
          stroke: disp[b].stroke,
          op: 0,
        },
  )
  animateTo(zoomT, 400, () => {
    // 提交本轮：进入 pivot bin、降一组 bit
    s.settled += v.above
    s.kCur -= v.above
    s.active = v.bins[pivot]
    if (s.shift === 0 || s.active.length <= s.kCur) {
      s.finished = true
      s.settled += s.active.length
      s.kCur = 0
    } else s.shift -= D
    s.round += 1

    // 阶段二：split —— 满窗口的块裂成 4 根新子柱
    const nv = view.value
    for (let b = 0; b < 4; b++)
      disp[b] = {
        x: fx,
        w: fullW,
        h: maxBarH,
        fill: fillFor(statusOf(b)),
        stroke: strokeFor(statusOf(b)),
        op: 1,
      }
    const splitT = [0, 1, 2, 3].map((b) => ({
      x: barX(b),
      w: barW,
      h: barH(nv.counts[b]),
      fill: fillFor(statusOf(b)),
      stroke: strokeFor(statusOf(b)),
      op: 1,
    }))
    requestAnimationFrame(() =>
      animateTo(splitT, 460, () => {
        transitioning.value = false
        syncDisp()
      }),
    )
  })
}

function reset() {
  if (raf) cancelAnimationFrame(raf)
  transitioning.value = false
  st.value = {
    active: vals.value.map((_, i) => i),
    kCur: k.value,
    shift: BITS - D,
    settled: 0,
    round: 0,
    finished: false,
  }
  syncDisp()
}
function shuffleData() {
  vals.value = distinctVals(N)
  reset()
}
function onK() {
  reset()
}
reset()
onBeforeUnmount(() => {
  if (raf) cancelAnimationFrame(raf)
})

const binLabel = (b) => b.toString(2).padStart(D, '0')
const bitHi = computed(() => st.value.shift + D - 1)
const bitLo = computed(() => st.value.shift)
</script>

<template>
  <div class="demo">
    <div class="bar">
      <div class="btns">
        <button @click="next" :disabled="st.finished || transitioning">下一轮 ▶</button>
        <button @click="reset">重置</button>
        <button @click="shuffleData">随机数据</button>
        <label class="kctl"
          >k = {{ k }}
          <input type="range" min="1" max="12" v-model.number="k" @input="onK" />
        </label>
      </div>
    </div>

    <div class="metrics">
      候选 <b>{{ st.active.length }}</b
      ><span class="sep">·</span> 已确定 top <b class="g">{{ st.settled }}</b
      ><span class="sep">·</span> 本轮还需 <b>{{ st.finished ? 0 : st.kCur }}</b
      ><span class="sep">·</span>
      <span v-if="!st.finished">当前检查 bit [{{ bitHi }}:{{ bitLo }}]</span>
      <span v-else class="g">完成：已锁定 top-{{ k }}</span>
    </div>

    <!-- 当前候选元素，按值降序，颜色 = 本轮命运 -->
    <div class="strip">
      <transition-group name="cell">
        <span
          v-for="item in activeSorted"
          :key="item.idx"
          class="vcell"
          :class="statusOf(item.b)"
          >{{ item.v }}</span
        >
      </transition-group>
    </div>

    <svg viewBox="0 0 560 184" class="stage">
      <text x="12" y="18" class="cap-lbl">
        {{
          transitioning
            ? '钻进 pivot bin，再按下一组 bit 重新分桶…'
            : '按高位 2 bit 分到 4 个 bin（左=最大）：'
        }}
      </text>
      <!-- 柱体（由 disp 驱动，递归时扩展/裂分） -->
      <rect
        v-for="b in 4"
        :key="'bar' + (b - 1)"
        :x="disp[b - 1].x"
        :y="baseY - disp[b - 1].h"
        :width="disp[b - 1].w"
        :height="disp[b - 1].h"
        rx="4"
        :fill="disp[b - 1].fill"
        :stroke="disp[b - 1].stroke"
        stroke-width="2"
        :opacity="disp[b - 1].op"
      />
      <!-- 标签：仅静止时显示（过渡中淡出，避免错位） -->
      <g class="labels" :style="{ opacity: transitioning ? 0 : 1 }">
        <g v-for="b in 4" :key="'lbl' + (b - 1)">
          <text :x="barX(b - 1) + barW / 2" :y="baseY - barH(view.counts[b - 1]) - 8" class="cnt">
            {{ view.counts[b - 1] }}
          </text>
          <text :x="barX(b - 1) + barW / 2" :y="baseY + 18" class="binlbl">
            {{ binLabel(b - 1) }}
          </text>
          <text
            :x="barX(b - 1) + barW / 2"
            :y="baseY + 34"
            class="fate"
            :fill="strokeFor(statusOf(b - 1))"
          >
            {{
              statusOf(b - 1) === 'top'
                ? '→ 进 top'
                : statusOf(b - 1) === 'pivot'
                  ? '→ 递归'
                  : '✕ 排除'
            }}
          </text>
        </g>
      </g>
    </svg>

    <p class="cap">
      不维护任何有序结构，只用<b>直方图</b>定位第 k 大落在哪个 bin：<b class="g">更高的 bin</b>
      整段进 top-k， <b>pivot bin</b> 含边界、只对它递归——下一轮<b>钻进这个 bin 内部</b>、按下一组
      bit 重新分成 4 桶，其余<b>排除</b>。每轮候选缩到约 1/4。 片上只需 4 个计数器、<b>与 k 无关</b
      >，所以再大的 k（甚至求中位数）都扛得住——代价是统计直方图要用原子。
    </p>
  </div>
</template>

<style scoped>
.demo {
  max-width: 640px;
  margin: 0 auto;
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #1f2329;
}
.bar {
  margin-bottom: 8px;
}
.btns {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.btns button {
  font: inherit;
  cursor: pointer;
  background: #fff;
  color: #1f2329;
  border: 1px solid #d9dee5;
  border-radius: 6px;
  padding: 5px 12px;
  font-size: 0.85rem;
}
.btns button:hover:not(:disabled) {
  border-color: #1677ff;
  color: #1677ff;
}
.btns button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  color: #1f2329;
  border-color: #d9dee5;
}
.kctl {
  font-size: 0.85rem;
  color: #5c6370;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: 4px;
}
.kctl input {
  vertical-align: middle;
  accent-color: #1677ff;
}
.metrics {
  font-size: 0.85rem;
  color: #8a909c;
  margin-bottom: 8px;
}
.metrics b {
  color: #1677ff;
  font-size: 1rem;
}
.metrics b.g {
  color: #2f8f6b;
}
.metrics .sep {
  margin: 0 8px;
}
.metrics .g {
  color: #2f8f6b;
}
.strip {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  min-height: 30px;
  padding: 8px;
  background: #f7f9fc;
  border: 1px solid #e7ecf3;
  border-radius: 10px 10px 0 0;
  border-bottom: none;
}
.vcell {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 26px;
  height: 22px;
  padding: 0 5px;
  border-radius: 4px;
  font-size: 0.78rem;
  font-weight: 600;
  transition: all 0.4s;
}
.vcell.top {
  background: #e6f2ec;
  color: #2f8f6b;
  border: 1px solid #86c4a6;
}
.vcell.pivot {
  background: rgba(22, 119, 255, 0.1);
  color: #0958d9;
  border: 1px solid #1677ff;
}
.vcell.excluded {
  background: #eef1f6;
  color: #a8b0bd;
  border: 1px solid #dbe1ea;
}
.cell-move {
  transition: transform 0.45s cubic-bezier(0.4, 0, 0.2, 1);
}
.cell-leave-active {
  position: absolute;
}
.cell-enter-from,
.cell-leave-to {
  opacity: 0;
  transform: scale(0.6);
}
.stage {
  width: 100%;
  background: #f7f9fc;
  border: 1px solid #e7ecf3;
  border-radius: 0 0 10px 10px;
}
.labels {
  transition: opacity 0.25s;
}
.cap-lbl {
  font-size: 11px;
  fill: #8a909c;
}
.cnt {
  text-anchor: middle;
  font-size: 12px;
  font-weight: 700;
  fill: #1f2329;
}
.binlbl {
  text-anchor: middle;
  font-size: 11px;
  font-weight: 600;
  fill: #5c6370;
  font-family: Consolas, 'Cascadia Mono', monospace;
}
.fate {
  text-anchor: middle;
  font-size: 10px;
  font-weight: 600;
}
.cap {
  font-size: 0.85rem;
  line-height: 1.7;
  color: #5c6370;
  margin-top: 10px;
}
.cap b {
  color: #1677ff;
}
.cap b.g {
  color: #389e0d;
}
</style>
