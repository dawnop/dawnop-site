<!-- viz-name: Bitonic 排序网络（数据无关、无分支） -->
<script setup>
import { ref, reactive, computed, onBeforeUnmount } from 'vue'

// n=8 的双调排序网络：比较器位置完全固定、与数据无关 —— 这正是它适合 SIMT 的原因。
// 值沿导线从左向右流动，每步前进一列；被比较器交换的一对斜向交叉、换到对方导线上。
const N = 8
const K = 4 // 末尾 K 条线即 top-k

const padX = 64
const gap = 60
const rowY0 = 40
const rowH = 34

// 预计算所有「阶段」（每个阶段 = 一列同时发生的比较-交换），与数据无关
function buildStages(n) {
  const stages = []
  for (let k = 2; k <= n; k *= 2) {
    for (let j = k >> 1; j > 0; j >>= 1) {
      const comps = []
      for (let i = 0; i < n; i++) {
        const l = i ^ j
        if (l > i) comps.push({ a: i, b: l, asc: (i & k) === 0, j })
      }
      stages.push(comps)
    }
  }
  return stages
}
const stages = buildStages(N)
const L = stages.length

let uid = 0
const randItems = () =>
  Array.from({ length: N }, () => ({ id: uid++, v: 10 + Math.floor(Math.random() * 89) }))
const items = ref(randItems()) // 稳定的 {id, v}
const order = ref(items.value.map((it) => it.id)) // order[行] = id（当前列各行是谁）
const pos = reactive({}) // id -> {x, y} 渲染坐标（rAF 更新）
const valById = computed(() => Object.fromEntries(items.value.map((it) => [it.id, it.v])))

const step = ref(0) // 当前所在列（0..L）
const animating = ref(false)
const done = computed(() => step.value >= L)

const rowY = (i) => rowY0 + i * rowH
const colX = (s) => padX + s * gap
// 同列内多个比较器的竖线会落在同一 x；按其在 bitonic 块内的「车道」(a % j) 加水平偏移，错开成并排
const SPREAD = 8
const compX = (c, s) => colX(s + 0.5) + ((c.a % c.j) - (c.j - 1) / 2) * SPREAD
// 高亮：动画中点亮正在穿越的那一列比较器；静止时点亮下一列
const litStage = computed(() => (animating.value ? step.value - 1 : done.value ? -1 : step.value))

const maxV = computed(() => Math.max(...items.value.map((i) => i.v)))
const hue = (v) => Math.round((v / maxV.value) * 215)

function placeAll() {
  order.value.forEach((id, r) => {
    pos[id] = { x: colX(step.value), y: rowY(r) }
  })
}
placeAll()

const HBOW = 18 // 交换对的水平 bow：在 y 交叉处把两盒错开，避免重合
let raf = null
function next() {
  if (animating.value || done.value) return
  const s = step.value
  const oldOrder = order.value
  const newOrder = oldOrder.slice()
  const swapped = new Set()
  for (const c of stages[s]) {
    const idA = oldOrder[c.a],
      idB = oldOrder[c.b]
    const hi = valById.value[idA] > valById.value[idB]
    // asc 段：大的去高索引（下方）；desc 段：大的去低索引（上方）
    if ((c.asc && hi) || (!c.asc && !hi)) {
      newOrder[c.a] = idB
      newOrder[c.b] = idA
      swapped.add(idA)
      swapped.add(idB)
    }
  }
  const oldRow = {}
  oldOrder.forEach((id, r) => (oldRow[id] = r))
  const newRow = {}
  newOrder.forEach((id, r) => (newRow[id] = r))
  // 每个值：列 s→s+1，旧行→新行；交换对加相反方向的水平 bow，斜向交叉而不重合
  const anims = oldOrder.map((id) => {
    const r0 = oldRow[id],
      r1 = newRow[id]
    const bow = swapped.has(id) ? (r1 > r0 ? HBOW : -HBOW) : 0
    return { id, x0: colX(s), y0: rowY(r0), x1: colX(s + 1), y1: rowY(r1), bow }
  })

  order.value = newOrder
  step.value = s + 1
  animating.value = true
  const dur = 520,
    start = performance.now()
  const tick = (now) => {
    const t = Math.min(1, (now - start) / dur)
    const e = t * t * (3 - 2 * t) // smoothstep
    const sinp = Math.sin(Math.PI * t)
    for (const a of anims)
      pos[a.id] = { x: a.x0 + (a.x1 - a.x0) * e + a.bow * sinp, y: a.y0 + (a.y1 - a.y0) * e }
    if (t < 1) raf = requestAnimationFrame(tick)
    else {
      for (const a of anims) pos[a.id] = { x: a.x1, y: a.y1 }
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
  items.value = randItems()
  order.value = items.value.map((it) => it.id)
  step.value = 0
  placeAll()
}
onBeforeUnmount(() => {
  if (raf) cancelAnimationFrame(raf)
})

const width = padX + L * gap + 30
const height = rowY0 + N * rowH + 6
</script>

<template>
  <div class="demo">
    <div class="bar">
      <div class="btns">
        <button @click="next" :disabled="done || animating">步进 ▶</button>
        <button @click="reset">重置</button>
        <button @click="shuffle">随机数据</button>
      </div>
      <div class="status">
        阶段 <b>{{ step }}/{{ L }}</b>
        <span class="sep">·</span>
        <span v-if="!done">交换的一对斜向交叉换线</span>
        <span v-else class="ok">已排序，末 {{ K }} 条线即 top-{{ K }}</span>
      </div>
    </div>

    <svg :viewBox="`0 0 ${width} ${height}`" class="stage">
      <!-- top-k 区域底色（末 K 条线） -->
      <rect
        :x="0"
        :y="rowY(N - K) - rowH / 2"
        :width="width"
        :height="K * rowH"
        :class="{ tkband: true, lit: done }"
      />

      <!-- 导线 -->
      <line
        v-for="i in N"
        :key="'w' + i"
        :x1="colX(0)"
        :x2="colX(L)"
        :y1="rowY(i - 1)"
        :y2="rowY(i - 1)"
        class="wire"
      />

      <!-- 所有比较器列（静态画出，当前穿越列高亮）。箭头表示大者去向 -->
      <g v-for="(comp, s) in stages" :key="'s' + s">
        <g v-for="(c, ci) in comp" :key="'c' + s + '-' + ci">
          <line
            :x1="compX(c, s)"
            :x2="compX(c, s)"
            :y1="rowY(c.a)"
            :y2="rowY(c.b)"
            class="cmp"
            :class="{ active: s === litStage }"
          />
          <circle
            :cx="compX(c, s)"
            :cy="rowY(c.a)"
            r="2.4"
            class="dot"
            :class="{ active: s === litStage }"
          />
          <circle
            :cx="compX(c, s)"
            :cy="rowY(c.b)"
            r="2.4"
            class="dot"
            :class="{ active: s === litStage }"
          />
          <polygon
            :points="
              c.asc
                ? `${compX(c, s) - 4},${rowY(c.b) - 8} ${compX(c, s) + 4},${rowY(c.b) - 8} ${compX(c, s)},${rowY(c.b) - 1}`
                : `${compX(c, s) - 4},${rowY(c.a) + 8} ${compX(c, s) + 4},${rowY(c.a) + 8} ${compX(c, s)},${rowY(c.a) + 1}`
            "
            class="arrow"
            :class="{ active: s === litStage }"
          />
        </g>
      </g>

      <!-- 值：沿导线流动，交换的一对斜向交叉 -->
      <g
        v-for="it in items"
        :key="it.id"
        class="cell"
        :style="{ transform: `translate(${pos[it.id].x}px, ${pos[it.id].y}px)` }"
      >
        <rect
          x="-15"
          y="-12"
          width="30"
          height="24"
          rx="5"
          :fill="`hsl(${hue(valById[it.id])},50%,92%)`"
          :stroke="`hsl(${hue(valById[it.id])},38%,64%)`"
        />
        <text class="num" :fill="`hsl(${hue(valById[it.id])},34%,42%)`">{{ valById[it.id] }}</text>
      </g>
    </svg>

    <p class="cap">
      8 条导线、固定的比较器网络：每一<b>列</b>是一组同时进行的「比较-交换」，箭头指向较大值的去向；
      值沿导线向右流动，被比较器交换的一对<b>斜向交叉</b>、换到对方导线上。 关键在于
      <b>比较器的位置和数据完全无关</b>——无论输入是什么都走同样的步骤，没有数据相关分支， 天生契合
      GPU 的 SIMT。代价是比较器数量 O(k·log²k)，所以只适合小 k。
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
.status {
  font-size: 0.85rem;
  color: #8a909c;
}
.status b {
  color: #1677ff;
}
.status .ok {
  color: #2f8f6b;
}
.status .sep {
  margin: 0 8px;
}
.stage {
  width: 100%;
  background: #f7f9fc;
  border: 1px solid #e7ecf3;
  border-radius: 10px;
}
.tkband {
  fill: transparent;
  transition: fill 0.4s;
}
.tkband.lit {
  fill: rgba(124, 193, 163, 0.15);
}
.wire {
  stroke: #cbd2dc;
  stroke-width: 1.5;
}
.cmp {
  stroke: #dde3ec;
  stroke-width: 2;
  transition: stroke 0.3s;
}
.cmp.active {
  stroke: #1677ff;
  stroke-width: 3;
}
.arrow {
  fill: #dde3ec;
  transition: fill 0.3s;
}
.arrow.active {
  fill: #1677ff;
}
.dot {
  fill: #d3dae4;
  transition: fill 0.3s;
}
.dot.active {
  fill: #1677ff;
}
.num {
  text-anchor: middle;
  dominant-baseline: central;
  font-size: 12px;
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
</style>
