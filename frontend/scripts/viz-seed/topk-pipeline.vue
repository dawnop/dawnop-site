<!-- viz-name: Bitonic Top-K 三级归约总览（warp → block → grid） -->
<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'

// 串联整条流水：输入按 warp 切片 → 每个 warp 流式循环维护 top-32 → block 内归并 → grid 间归并。
// 示例参数：n=768，k=32，blocks=2，warps/block=3 → 总 warp=6；每 warp 切片 128 个，每轮 32 个 → R=4 轮。
const N_TOTAL = 768
const K = 32
const WPB = 3 // warps / block
const WARPS = 6
const BLOCKS = WARPS / WPB
const SLICE = N_TOTAL / WARPS // 每 warp 负责的元素数 = 128
const R = SLICE / K // 每 warp 的流式轮数 = 4（= chunk 数）
const blockOf = (i) => Math.floor(i / WPB)

const left = 72,
  right = 24,
  W = 620
const slot = (W - left - right) / WARPS
const xWarp = (i) => left + (i + 0.5) * slot
const sliceX0 = (i) => left + i * slot
const xBlock = (b) => left + (b * WPB + WPB / 2) * slot
const xFinal = (xBlock(0) + xBlock(BLOCKS - 1)) / 2

const yIn = 38,
  barH = 24,
  inB = yIn + barH
const yWarpC = 122,
  warpH = 38,
  warpT = yWarpC - warpH / 2,
  warpB = yWarpC + warpH / 2
const yBlockC = 202,
  blockH = 38,
  blockT = yBlockC - blockH / 2,
  blockB = yBlockC + blockH / 2
const yFinalC = 278,
  finalH = 44,
  finalT = yFinalC - finalH / 2

const step = ref(0)
const MAX = 3
const next = () => {
  if (step.value < MAX) step.value++
}
const reset = () => {
  step.value = 0
}
const done = computed(() => step.value >= MAX)
const st = (lvl) => (step.value < lvl ? 'idle' : step.value === lvl ? 'active' : 'done')

const warps = Array.from({ length: WARPS }, (_, i) => i)
const blocks = Array.from({ length: BLOCKS }, (_, b) => b)
const chunks = Array.from({ length: R }, (_, c) => c)
const chunkW = (slot - 4) / R

// warp 级流式循环：round 在 step===1 时自动 1..R 循环推进（展示"多执行几次"）
const round = ref(1)
let timer = null
function startLoop() {
  stopLoop()
  round.value = 1
  timer = setInterval(() => {
    round.value = (round.value % R) + 1
  }, 650)
}
function stopLoop() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}
watch(step, (s) => {
  if (s === 1) startLoop()
  else stopLoop()
})
onBeforeUnmount(stopLoop)

// 每个 chunk 的状态：step1 时按当前 round 显示"已并入 / 当前轮 / 待处理"，循环往复
function chunkState(c) {
  if (step.value === 0) return 'input'
  if (step.value === 1)
    return c < round.value - 1 ? 'done' : c === round.value - 1 ? 'active' : 'idle'
  return 'done'
}

// warp 节点上的自循环弧（表示 merge_top32 被反复调用）
const loopPath = (i) => {
  const cx = xWarp(i),
    t = warpT - 1
  return `M ${cx - 7} ${t} C ${cx - 16} ${t - 20} ${cx + 16} ${t - 20} ${cx + 7} ${t}`
}

const caption = computed(() => {
  switch (step.value) {
    case 0:
      return `输入 n=${N_TOTAL} 按 warp 切片：每 warp 负责 ${SLICE} 个，再按每轮 ${K} 个分成 ${R} 段`
    case 1:
      return `① warp 级：流式循环，第 ${round.value}/${R} 轮——取 ${K} 个候选，sort → merge 进 running`
    case 2:
      return '② block 级：shared 内把同 block 各 warp 的 top-32 归并为 block 的 top-32'
    default:
      return '③ grid 级：各 block 的 top-32 最终归并 → 全局 top-32'
  }
})
</script>

<template>
  <div class="demo">
    <div class="bar">
      <div class="btns">
        <button @click="next" :disabled="done">步进 ▶</button>
        <button @click="reset">重置</button>
      </div>
      <div class="status" :class="{ ok: done }">{{ caption }}</div>
    </div>
    <div class="params">
      示例参数：<b>n=768</b> · <b>k=32</b> · blocks=2 · warps/block=3 · 总 warp=6 · 每 warp 切片 128
      个 · 每轮 32 个 → <b>R=4 轮</b>
    </div>

    <svg :viewBox="`0 0 ${W} 320`" class="stage">
      <defs>
        <marker id="pl-arrow" markerWidth="7" markerHeight="7" refX="4.5" refY="3" orient="auto">
          <path d="M0,0 L5,3 L0,6 Z" />
        </marker>
        <marker id="pl-loop" markerWidth="7" markerHeight="7" refX="4" refY="3" orient="auto">
          <path d="M0,0 L5,3 L0,6 Z" />
        </marker>
      </defs>

      <!-- 左侧行标签 -->
      <g class="rowlbl">
        <text :x="8" :y="yIn + barH / 2 + 4" :class="st(0)">输入</text>
        <text :x="8" :y="yWarpC + 4" :class="st(1)">warp 级</text>
        <text :x="8" :y="yBlockC + 4" :class="st(2)">block 级</text>
        <text :x="8" :y="yFinalC + 4" :class="st(3)">grid 级</text>
      </g>

      <!-- 输入条：每个 warp 切片再细分成 R 个 chunk（每轮 32 个） -->
      <g>
        <template v-for="i in warps" :key="'sl' + i">
          <rect
            v-for="c in chunks"
            :key="'sl' + i + '-' + c"
            :x="sliceX0(i) + 2 + c * chunkW"
            :y="yIn"
            :width="chunkW - 1.5"
            :height="barH"
            rx="2"
            class="slice"
            :class="chunkState(c)"
          />
        </template>
        <text :x="xFinal" :y="yIn - 6" class="cnt">
          n = {{ N_TOTAL }} 个元素（每 warp 切片 = {{ SLICE }}，分 {{ R }} 段）
        </text>
      </g>

      <!-- L1 箭头：当前轮的 chunk → warp -->
      <line
        v-for="i in warps"
        :key="'a1' + i"
        :x1="xWarp(i)"
        :y1="inB"
        :x2="xWarp(i)"
        :y2="warpT - 3"
        class="arrow"
        :class="st(1)"
        marker-end="url(#pl-arrow)"
      />
      <!-- warp 自循环弧：表示 merge_top32 反复调用 R 轮 -->
      <path
        v-for="i in warps"
        :key="'lp' + i"
        :d="loopPath(i)"
        class="loop"
        :class="st(1)"
        marker-end="url(#pl-loop)"
      />
      <!-- warp 节点 -->
      <g v-for="i in warps" :key="'w' + i">
        <rect
          :x="xWarp(i) - slot / 2 + 6"
          :y="warpT"
          :width="slot - 12"
          :height="warpH"
          rx="7"
          class="node"
          :class="st(1)"
        />
        <text :x="xWarp(i)" :y="yWarpC + 4" class="ntxt" :class="st(1)">top-32</text>
      </g>
      <text :x="xFinal" :y="warpT - 26" class="op" :class="st(1)">
        warp 内 · 流式 sort → merge · 每 warp ×{{ R }} 轮
      </text>

      <!-- L2 箭头：warp → block -->
      <line
        v-for="i in warps"
        :key="'a2' + i"
        :x1="xWarp(i)"
        :y1="warpB"
        :x2="xBlock(blockOf(i))"
        :y2="blockT - 3"
        class="arrow"
        :class="st(2)"
        marker-end="url(#pl-arrow)"
      />
      <!-- block 节点 -->
      <g v-for="b in blocks" :key="'b' + b">
        <rect
          :x="xBlock(b) - 52"
          :y="blockT"
          :width="104"
          :height="blockH"
          rx="8"
          class="node"
          :class="st(2)"
        />
        <text :x="xBlock(b)" :y="yBlockC + 4" class="ntxt" :class="st(2)">block top-32</text>
      </g>
      <text :x="xFinal" :y="blockT - 13" class="op" :class="st(2)">block 内 · merge_top32</text>

      <!-- L3 箭头：block → final -->
      <line
        v-for="b in blocks"
        :key="'a3' + b"
        :x1="xBlock(b)"
        :y1="blockB"
        :x2="xFinal"
        :y2="finalT - 3"
        class="arrow"
        :class="st(3)"
        marker-end="url(#pl-arrow)"
      />
      <!-- final 节点 -->
      <rect
        :x="xFinal - 70"
        :y="finalT"
        :width="140"
        :height="finalH"
        rx="9"
        class="node final"
        :class="st(3)"
      />
      <text :x="xFinal" :y="yFinalC - 2" class="ntxt big" :class="st(3)">全局 top-32</text>
      <text :x="xFinal" :y="yFinalC + 14" class="sub" :class="st(3)">grid 间 · merge_top32</text>
    </svg>

    <p class="cap">
      整条流水是一个<b>三级归约</b>：输入按 warp <b>切片</b>，每个 warp
      用一个<b>流式循环</b>把自己的切片每 {{ K }} 个一批、共 {{ R }} 轮地 merge 进 register 内的
      top-32； 同一 block 的若干 warp 在 shared 内<b>归并</b>成 block 的 top-32；各 block 的结果再在
      grid 级归并成全局 top-32。 三级复用同一个 <b>merge_top32</b> 原语。注意
      <b>sort 是 merge_top32 的第一步</b>：warp
      级对原始候选的排序是必需的（数据的实际排序就发生在循环的每一轮里）， block / grid
      级的输入已有序，那次内部 sort 冗余但无害。全程只有读输入是访存，其余在片上完成，无
      atomic、无数据相关 branch。
    </p>
  </div>
</template>

<style scoped>
.demo {
  max-width: 660px;
  margin: 0 auto;
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #1f2329;
}
.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
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
}
.status.ok {
  color: #2f8f6b;
}
.params {
  font-size: 0.76rem;
  color: #8a909c;
  background: #f2f5fa;
  border: 1px solid #e7ecf3;
  border-radius: 6px;
  padding: 4px 10px;
  margin-bottom: 8px;
}
.params b {
  color: #1677ff;
  font-weight: 600;
}
.stage {
  width: 100%;
  background: #f7f9fc;
  border: 1px solid #e7ecf3;
  border-radius: 10px;
}
.rowlbl text {
  font-size: 11px;
  fill: #b3bac6;
  transition: fill 0.3s;
}
.rowlbl text.active {
  fill: #1677ff;
}
.rowlbl text.done {
  fill: #2f8f6b;
}

/* 输入 chunk */
.slice {
  fill: #eef2f8;
  stroke: #d4dcea;
  stroke-width: 1;
  transition: all 0.25s;
}
.slice.input {
  fill: #e8f1ff;
  stroke: #9cc1f5;
}
.slice.active {
  fill: #1677ff;
  stroke: #1677ff;
}
.slice.done {
  fill: #eaf4ef;
  stroke: #b6dcc8;
}

/* 节点 */
.node {
  fill: #eef1f6;
  stroke: #d3dae4;
  stroke-width: 1.4;
  transition: all 0.3s;
}
.node.active {
  fill: #e8f1ff;
  stroke: #1677ff;
}
.node.done {
  fill: #eaf4ef;
  stroke: #86c4a6;
}
.node.final.active {
  fill: #e6f7ef;
  stroke: #2f8f6b;
  stroke-width: 2;
}

.ntxt {
  text-anchor: middle;
  font-size: 12px;
  font-weight: 600;
  fill: #aab2bf;
  transition: fill 0.3s;
}
.ntxt.active {
  fill: #1677ff;
}
.ntxt.done {
  fill: #2f8f6b;
}
.ntxt.big {
  font-size: 13px;
}
.sub {
  text-anchor: middle;
  font-size: 10px;
  fill: #c2c9d4;
  transition: fill 0.3s;
}
.sub.active {
  fill: #4f9d7d;
}

/* 箭头 */
.arrow {
  stroke: #dfe4ec;
  stroke-width: 1.6;
  fill: none;
  transition: stroke 0.3s;
}
.arrow.active {
  stroke: #1677ff;
  stroke-dasharray: 5 4;
  animation: pl-flow 0.7s linear infinite;
}
.arrow.done {
  stroke: #aedcc6;
}

/* 自循环弧 */
.loop {
  fill: none;
  stroke: #d6dde7;
  stroke-width: 1.6;
  transition: stroke 0.3s;
}
.loop.active {
  stroke: #1677ff;
  stroke-dasharray: 4 3;
  animation: pl-flow 0.6s linear infinite;
}
.loop.done {
  stroke: #b6dcc8;
}
#pl-loop path {
  fill: #cbd2dc;
}
.loop.active ~ * {
} /* noop */

marker path {
  fill: #cbd2dc;
}

/* 操作标签 */
.op {
  text-anchor: middle;
  font-size: 10.5px;
  fill: #c2c9d4;
  font-weight: 600;
  transition: fill 0.3s;
}
.op.active {
  fill: #1677ff;
}
.op.done {
  fill: #5fa888;
}
.cnt {
  text-anchor: middle;
  font-size: 10px;
  fill: #aab2bf;
}

@keyframes pl-flow {
  to {
    stroke-dashoffset: -9;
  }
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
