<!-- viz-name: 从 sort 到 Top-K：分块 bitonic merge -->
<script setup>
import { ref, computed } from 'vue'

// 维护容量 k 的有序 top-k：当前 top-k(降序) 与 k 个新候选(升序) 逐位取大得到「较大半」，
// 它是一个 bitonic 序列，再做一遍 bitonic merge 即排成降序 —— 就是新的 top-k。
// 取 k=8 便于观察，规律与 k=32 一致。用柱高表示数值，一眼看出 bitonic 的「先降后升」。
const K = 8
const jList = [4, 2, 1] // bitonic merge 各阶段步距
const maxStep = 1 + jList.length // 0:双行  1:取大  2..:逐阶段 merge

const padX = 26
const cellW = 46
const barW = 30
const barMaxH = 56
const yTop = 92 // 上行(running)基线
const yBot = 198 // 下行(candidates)基线
const yMid = 150 // 合并后单行基线

const rnd = () => 10 + Math.floor(Math.random() * 89)
function mkData() {
  const r = Array.from({ length: K }, rnd).sort((a, b) => b - a) // top-k：降序
  const c = Array.from({ length: K }, rnd).sort((a, b) => a - b) // 候选：升序
  return {
    running: r.map((v, i) => ({ id: 'r' + i, v })),
    cand: c.map((v, i) => ({ id: 'c' + i, v })),
  }
}
const data = ref(mkData())
const step = ref(0)

const allBars = computed(() => [...data.value.running, ...data.value.cand])
const maxV = computed(() => Math.max(...allBars.value.map((b) => b.v)))
const hue = (v) => Math.round((v / maxV.value) * 215)
const barH = (v) => 10 + (v / maxV.value) * (barMaxH - 10)
const xAt = (i) => padX + i * cellW + cellW / 2

// 逐位取大：第 i 位胜者（running 胜平局）。结果长度 K，是 bitonic 序列。
const winners = computed(() =>
  Array.from({ length: K }, (_, i) => {
    const a = data.value.running[i]
    const b = data.value.cand[i]
    return a.v >= b.v ? a : b
  })
)
const winnerIds = computed(() => new Set(winners.value.map((w) => w.id)))

// 一个 bitonic merge 阶段（步距 j）：配对 (i, i^j)，降序——低位放大者、高位放小者。
function mergeStage(arr, j) {
  const out = arr.slice()
  for (let i = 0; i < arr.length; i++) {
    const l = i ^ j
    if (l > i) {
      const hi = arr[i].v >= arr[l].v
      out[i] = hi ? arr[i] : arr[l]
      out[l] = hi ? arr[l] : arr[i]
    }
  }
  return out
}
// 各 merge 阶段后的顺序：index 0 = 取大后(bitonic)，之后每应用一个 j 递进
const mergeOrders = computed(() => {
  const res = [winners.value]
  let cur = winners.value
  for (const j of jList) {
    cur = mergeStage(cur, j)
    res.push(cur)
  }
  return res
})

// 每个 bar 在当前 step 的渲染状态：{x, baseY, op}
const layout = computed(() => {
  const m = {}
  if (step.value === 0) {
    data.value.running.forEach((b, i) => (m[b.id] = { x: xAt(i), y: yTop, op: 1 }))
    data.value.cand.forEach((b, i) => (m[b.id] = { x: xAt(i), y: yBot, op: 1 }))
  } else {
    const order = mergeOrders.value[step.value - 1]
    order.forEach((b, pos) => (m[b.id] = { x: xAt(pos), y: yMid, op: 1 }))
    // 落选者：在原行位置淡出
    data.value.running.forEach((b, i) => {
      if (!winnerIds.value.has(b.id)) m[b.id] = { x: xAt(i), y: yTop, op: 0 }
    })
    data.value.cand.forEach((b, i) => {
      if (!winnerIds.value.has(b.id)) m[b.id] = { x: xAt(i), y: yBot, op: 0 }
    })
  }
  return m
})

// step 0：逐位取大的配对竖线
const fmaxLinks = computed(() => (step.value === 0 ? Array.from({ length: K }, (_, i) => i) : []))
// merge 阶段：当前 j 的配对弧线（画在合并行下方）
const activeArcs = computed(() => {
  if (step.value < 2) return []
  const j = jList[step.value - 2]
  const arcs = []
  for (let i = 0; i < K; i++) {
    const l = i ^ j
    if (l > i) {
      const y = yMid + 9
      const d = 8 + Math.abs(l - i) * 4
      arcs.push(`M ${xAt(i)} ${y} Q ${(xAt(i) + xAt(l)) / 2} ${y + d} ${xAt(l)} ${y}`)
    }
  }
  return arcs
})

const done = computed(() => step.value >= maxStep)
const label = computed(() => {
  switch (step.value) {
    case 0:
      return '当前 top-k(降序) 与 新候选(升序)：逐位比较'
    case 1:
      return '逐位取较大者 → 较大半（长度 k，是一个 bitonic 序列）'
    case maxStep:
      return '完成：已排成降序，即新的 top-k'
    default:
      return `bitonic merge：步距 j=${jList[step.value - 2]} 配对比较-交换`
  }
})

const next = () => { if (step.value < maxStep) step.value++ }
const reset = () => { step.value = 0 }
const shuffle = () => { data.value = mkData(); step.value = 0 }

const width = padX * 2 + K * cellW
const height = 232
</script>

<template>
  <div class="demo">
    <div class="bar">
      <div class="btns">
        <button @click="next" :disabled="done">步进 ▶</button>
        <button @click="reset">重置</button>
        <button @click="shuffle">随机数据</button>
      </div>
      <div class="status" :class="{ ok: done }">{{ label }}</div>
    </div>

    <svg :viewBox="`0 0 ${width} ${height}`" class="stage">
      <!-- 行标签（靠右放，避开左侧的高柱）（仅双行阶段） -->
      <g v-if="step === 0" class="rowlbl">
        <text :x="width - padX" :y="yTop - barMaxH - 6" text-anchor="end">top-k · 降序</text>
        <text :x="padX" :y="yBot + 16">candidates · 升序</text>
      </g>
      <text v-else-if="done" :x="width - padX" :y="yMid - barMaxH - 8" text-anchor="end" class="rowlbl ok">新 top-k · 降序</text>

      <!-- 逐位取大的配对竖线 -->
      <line
        v-for="i in fmaxLinks" :key="'f' + i"
        :x1="xAt(i)" :x2="xAt(i)" :y1="yTop + 4" :y2="yBot - barMaxH - 4"
        class="fmax"
      />

      <!-- merge 阶段配对弧线 -->
      <path v-for="(d, ai) in activeArcs" :key="'a' + ai" :d="d" class="arc" />

      <!-- 柱子：高度=数值，位置随 step 平滑移动 -->
      <g
        v-for="b in allBars" :key="b.id"
        class="cell"
        :style="{ transform: `translate(${layout[b.id].x}px, ${layout[b.id].y}px)`, opacity: layout[b.id].op }"
      >
        <rect
          :x="-barW / 2" :y="-barH(b.v)" :width="barW" :height="barH(b.v)" rx="4"
          :fill="`hsl(${hue(b.v)},50%,92%)`" :stroke="`hsl(${hue(b.v)},38%,64%)`"
        />
        <text :y="-barH(b.v) - 5" class="num" :fill="`hsl(${hue(b.v)},34%,42%)`">{{ b.v }}</text>
      </g>
    </svg>

    <p class="cap">
      维护容量 k 的有序 top-k：把当前 <b>top-k(降序)</b> 与 k 个 <b>新候选(升序)</b> 逐位取较大者，
      得到的「较大半」恰是一个 <b>bitonic 序列</b>；对它做一遍 bitonic merge（步距 j 折半），即排回降序——
      就是新的 top-k。全程比较位置只与下标有关、与数据无关，可在 warp 内用 <b>__shfl_xor_sync</b> 无 branch 完成。
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
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
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
  margin-right: 6px;
}
.btns button:hover:not(:disabled) { border-color: #1677ff; color: #1677ff; }
.btns button:disabled { opacity: 0.45; cursor: not-allowed; }
.status { font-size: 0.82rem; color: #8a909c; font-variant-numeric: tabular-nums; }
.status.ok { color: #2f8f6b; }
.stage {
  width: 100%;
  background: #f7f9fc;
  border: 1px solid #e7ecf3;
  border-radius: 10px;
}
.rowlbl text, .rowlbl { font-size: 11px; fill: #8a909c; }
.rowlbl.ok { fill: #2f8f6b; }
.fmax { stroke: #cdd5e0; stroke-width: 1.5; stroke-dasharray: 3 3; }
.arc { fill: none; stroke: #1677ff; stroke-width: 2; opacity: 0.85; }
.cell { transition: transform 0.5s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.4s; }
.num { text-anchor: middle; font-size: 11px; font-weight: 600; }
.cap {
  font-size: 0.85rem;
  line-height: 1.7;
  color: #5c6370;
  margin-top: 10px;
}
.cap b { color: #1677ff; }
</style>
