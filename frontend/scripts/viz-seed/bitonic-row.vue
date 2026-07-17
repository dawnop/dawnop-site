<!-- viz-name: Bitonic sort 一行数字逐阶段动画 -->
<script setup>
import { ref, reactive, computed, onBeforeUnmount } from 'vue'

// 一行 8 根柱子（柱高=数值）逐阶段做 compare-exchange：需要交换的两根水平滑到对方位置；
// 比较位置（连线）由 i XOR j 固定决定，与数据无关。
const N = 8
const padX = 22
const cellW = 48
const barW = 30
const barBase = 104 // 柱子底边基线
const barMaxH = 44
const W = padX * 2 + N * cellW
const cx = (i) => padX + i * cellW + cellW / 2

function buildStages(n) {
  const stages = []
  for (let k = 2; k <= n; k <<= 1) {
    for (let j = k >> 1; j > 0; j >>= 1) {
      const comps = []
      for (let i = 0; i < n; i++) {
        const l = i ^ j
        if (l > i) comps.push({ a: i, b: l, asc: (i & k) === 0, j, k })
      }
      stages.push(comps)
    }
  }
  return stages
}
const stages = buildStages(N)

let uid = 0
const mkItems = () =>
  Array.from({ length: N }, () => ({ id: uid++, v: 10 + Math.floor(Math.random() * 89) }))
const items = ref(mkItems()) // 稳定的 {id, v}：值绑定到 id，排序是“位置”重排
const order = ref(items.value.map((it) => it.id)) // 当前位置 → id
const pos = reactive({}) // id → {x, y} 渲染坐标（动画期间被 rAF 更新）
const valById = computed(() => Object.fromEntries(items.value.map((it) => [it.id, it.v])))
const maxV = computed(() => Math.max(...items.value.map((i) => i.v)))
const hue = (v) => Math.round((v / maxV.value) * 215)
const barH = (v) => 8 + (v / maxV.value) * (barMaxH - 8)
function placeAll() {
  order.value.forEach((id, i) => {
    pos[id] = { x: cx(i), y: barBase }
  })
}
placeAll()

const step = ref(0)
const animating = ref(false)
const active = computed(() => (step.value < stages.length ? stages[step.value] : []))
const done = computed(() => step.value >= stages.length)

// 比较器连线（画在柱子下方）：箭头指向“较大值应去的格子”（asc→高 index，desc→低 index）
function connPath(c) {
  const dst = c.asc ? c.b : c.a
  const src = c.asc ? c.a : c.b
  const y = barBase + 8
  const depth = 6 + Math.abs(c.b - c.a) * 4
  return `M ${cx(src)} ${y} Q ${(cx(src) + cx(dst)) / 2} ${y + depth} ${cx(dst)} ${y}`
}

let raf = null
function next() {
  if (animating.value || done.value) return
  const stage = stages[step.value]
  const newOrder = order.value.slice()
  const anims = []
  for (const c of stage) {
    const idA = order.value[c.a],
      idB = order.value[c.b]
    const hi = valById.value[idA] > valById.value[idB]
    // asc：大的去高 index；desc：大的去低 index。需要交换则两根柱子水平滑到对方位置
    if ((c.asc && hi) || (!c.asc && !hi)) {
      newOrder[c.a] = idB
      newOrder[c.b] = idA
      anims.push({ id: idA, x0: cx(c.a), x1: cx(c.b) }) // a→b
      anims.push({ id: idB, x0: cx(c.b), x1: cx(c.a) }) // b→a
    }
  }
  order.value = newOrder
  step.value++
  if (!anims.length) return
  animating.value = true
  const dur = 420,
    start = performance.now()
  const ease = (t) => t * t * (3 - 2 * t) // smoothstep
  const tick = (now) => {
    const t = ease(Math.min(1, (now - start) / dur))
    for (const a of anims) pos[a.id] = { x: a.x0 + (a.x1 - a.x0) * t, y: barBase }
    if (t < 1) raf = requestAnimationFrame(tick)
    else {
      for (const a of anims) pos[a.id] = { x: a.x1, y: barBase }
      animating.value = false
    }
  }
  raf = requestAnimationFrame(tick)
}
function reset() {
  if (raf) cancelAnimationFrame(raf)
  animating.value = false
  order.value = items.value.map((it) => it.id)
  step.value = 0
  placeAll()
}
function shuffle() {
  if (raf) cancelAnimationFrame(raf)
  animating.value = false
  items.value = mkItems()
  order.value = items.value.map((it) => it.id)
  step.value = 0
  placeAll()
}
onBeforeUnmount(() => {
  if (raf) cancelAnimationFrame(raf)
})

const stageLabel = computed(() => {
  if (done.value) return '已排序'
  const c = active.value[0]
  return `阶段 ${step.value + 1}/${stages.length} · k=${c.k} j=${c.j} · partner = i XOR ${c.j}`
})
</script>

<template>
  <div class="demo">
    <div class="bar">
      <div class="btns">
        <button @click="next" :disabled="done || animating">步进 ▶</button>
        <button @click="reset">重置</button>
        <button @click="shuffle">随机数据</button>
      </div>
      <div class="status" :class="{ ok: done }">{{ stageLabel }}</div>
    </div>

    <svg :viewBox="`0 0 ${W} 176`" class="stage">
      <defs>
        <marker id="brow-arrow" markerWidth="8" markerHeight="8" refX="5" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" class="mk" />
        </marker>
      </defs>

      <!-- 比较器连线（下方）：箭头朝向较大值的去向 -->
      <path
        v-for="(c, ci) in active"
        :key="'c' + ci"
        :d="connPath(c)"
        class="conn"
        marker-end="url(#brow-arrow)"
      />

      <!-- 一行柱子：柱高=数值，绑定到 id；位置由 pos 决定，交换时水平滑到对方位置 -->
      <g
        v-for="it in items"
        :key="it.id"
        :transform="pos[it.id] ? `translate(${pos[it.id].x},${pos[it.id].y})` : ''"
      >
        <rect
          :x="-barW / 2"
          :y="-barH(it.v)"
          :width="barW"
          :height="barH(it.v)"
          rx="4"
          :fill="`hsl(${hue(it.v)},50%,92%)`"
          :stroke="`hsl(${hue(it.v)},38%,64%)`"
        />
        <text x="0" :y="-barH(it.v) - 5" class="num" :fill="`hsl(${hue(it.v)},34%,40%)`">
          {{ it.v }}
        </text>
      </g>

      <text :x="padX" y="170" class="lbl">index 0 → {{ N - 1 }}（升序排好后末尾即最大）</text>
    </svg>

    <p class="cap">
      一行柱子逐<b>阶段</b>做
      compare-exchange：下方连线是本阶段比较的下标对，箭头指向较大值应去的位置；
      需要交换的两根柱子<b>水平滑动</b>到对方位置。比较的下标对由
      <b>i XOR j</b> 固定决定、与数据无关—— 这正是它能在 warp 内用 <b>__shfl_xor_sync</b> 无 branch
      实现的原因。
    </p>
  </div>
</template>

<style scoped>
.demo {
  max-width: 560px;
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
.btns button:hover:not(:disabled) {
  border-color: #1677ff;
  color: #1677ff;
}
.btns button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.status {
  font-size: 0.82rem;
  color: #8a909c;
  font-variant-numeric: tabular-nums;
}
.status.ok {
  color: #2f8f6b;
}
.stage {
  width: 100%;
  background: #f7f9fc;
  border: 1px solid #e7ecf3;
  border-radius: 10px;
}
.conn {
  fill: none;
  stroke: #1677ff;
  stroke-width: 2;
  opacity: 0.9;
}
.mk {
  fill: #1677ff;
}
.num {
  text-anchor: middle;
  font-size: 12px;
  font-weight: 600;
}
.lbl {
  font-size: 11px;
  fill: #8a909c;
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
</style>
