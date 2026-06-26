<!-- viz-name: 二分阈值 Top-K 总览（一 warp 一行、register 内二分） -->
<script setup>
import { ref, computed } from 'vue'

// 串联批量结构：B 行（一 warp 一行，全部 lockstep）→ 载入 register + 求 [lo,hi] →
// 二分 count_ge 循环（×ITERS 轮，无尾部发散）→ 阈值 τ → ballot 收集 top-k。
const PASSES = 4 // 二分轮数（示意，实际 ITERS≈8）
const W = 640

const yBox = 64, boxH = 48, boxT = yBox - boxH / 2, boxB = yBox + boxH / 2
const nodes = [
  { id: 'load', x: 232, w: 120, label: '载入 vals[L] · 求 [lo,hi]', tag: '' },
  { id: 'bisect', x: 396, w: 120, label: '二分 count_ge', tag: 'ballot + popc' },
  { id: 'collect', x: 548, w: 108, label: 'Collect', tag: 'ballot prefix-sum' },
]
const stackX = 70, stackW = 70

const step = ref(0)
const next = () => { if (step.value < PASSES) step.value++ }
const reset = () => { step.value = 0 }
const done = computed(() => step.value >= PASSES)

const loadSt = computed(() => (step.value === 0 ? 'idle' : step.value === 1 ? 'active' : 'done'))
const bisectSt = computed(() => (step.value === 0 ? 'idle' : step.value < PASSES ? 'active' : 'done'))
const collectSt = computed(() => (step.value < PASSES ? 'idle' : 'active'))
const stOf = (id) => (id === 'load' ? loadSt.value : id === 'bisect' ? bisectSt.value : collectSt.value)

// 二分区间 [lo,hi] 随 step 收窄，τ = 中点（固定逼近 target）
const AX0 = 150, AX1 = 506, axY = 150
const target = 0.62
const widthFrac = computed(() => (step.value === 0 ? 1 : Math.pow(0.5, step.value)))
const fx = (f) => AX0 + (AX1 - AX0) * Math.max(0, Math.min(1, f))
const xLo = computed(() => fx(target - widthFrac.value / 2))
const xHi = computed(() => fx(target + widthFrac.value / 2))
const xTau = computed(() => fx(target))

const arrow = (x1, x2) => `M ${x1} ${yBox} L ${x2} ${yBox}`
const caption = computed(() => {
  if (step.value === 0) return '起点：B 行各由一个 warp 处理，整行载入 register，初始区间 = [行 min, 行 max]'
  if (step.value < PASSES) return `二分第 ${step.value} 轮：count_ge(τ) 与 k 比较 → 收窄 [lo,hi]（所有 warp 同步推进）`
  return '收敛：count_ge(τ) = k → 阈值 τ 定，ballot 前缀和紧凑收集 top-k'
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
      示例参数：<b>B 行</b> · <b>R</b>/行 · L = R/32 · 一 warp 一行 · 二分 <b>×ITERS≈8 轮</b>（lockstep，无尾部发散）· 与 <b>k 无关</b>
    </div>

    <svg :viewBox="`0 0 ${W} 196`" class="stage">
      <defs>
        <marker id="th-arrow" markerWidth="7" markerHeight="7" refX="4.5" refY="3" orient="auto">
          <path d="M0,0 L5,3 L0,6 Z" />
        </marker>
      </defs>

      <!-- 批量：B 行堆叠（一 warp 一行） -->
      <g>
        <rect v-for="r in [4, 3, 2, 1, 0]" :key="'st' + r"
          :x="stackX - stackW / 2 + r * 4" :y="boxT - 8 + r * 4" :width="stackW" :height="boxH - 6" rx="6"
          class="rowcard" :class="{ front: r === 0 }" />
        <text :x="stackX + 4" :y="yBox + 2" class="ntxt front">B 行</text>
        <text :x="stackX + 4" :y="boxB + 18" class="tag on">一 warp 一行</text>
      </g>
      <path :d="arrow(stackX + stackW / 2 + 6, nodes[0].x - nodes[0].w / 2)" class="arrow" :class="loadSt" marker-end="url(#th-arrow)" />

      <!-- 连接箭头 -->
      <path :d="arrow(nodes[0].x + nodes[0].w / 2, nodes[1].x - nodes[1].w / 2)" class="arrow" :class="bisectSt" marker-end="url(#th-arrow)" />
      <path :d="arrow(nodes[1].x + nodes[1].w / 2, nodes[2].x - nodes[2].w / 2)" class="arrow" :class="collectSt" marker-end="url(#th-arrow)" />

      <!-- 二分自循环弧 -->
      <path
        :d="`M ${nodes[1].x - 7} ${boxT - 1} C ${nodes[1].x - 16} ${boxT - 22} ${nodes[1].x + 16} ${boxT - 22} ${nodes[1].x + 7} ${boxT - 1}`"
        class="loop" :class="bisectSt" marker-end="url(#th-arrow)" />
      <text :x="nodes[1].x" :y="boxT - 26" class="op" :class="bisectSt">×ITERS 轮 · lockstep</text>

      <!-- 三个处理节点 -->
      <g v-for="n in nodes" :key="n.id">
        <rect :x="n.x - n.w / 2" :y="boxT" :width="n.w" :height="boxH" rx="8" class="node" :class="stOf(n.id)" />
        <text :x="n.x" :y="n.tag ? yBox - 2 : yBox + 4" class="ntxt" :class="stOf(n.id)">{{ n.label }}</text>
        <text v-if="n.tag" :x="n.x" :y="yBox + 13" class="tag" :class="stOf(n.id)">{{ n.tag }}</text>
      </g>
      <text :x="nodes[2].x" :y="boxB + 17" class="tag on">→ top-k</text>

      <!-- 二分区间轴 [lo, τ, hi]，随 step 收窄 -->
      <text :x="(AX0 + AX1) / 2" :y="axY - 14" class="axlbl" :class="bisectSt">
        {{ done ? 'count_ge(τ) = k ✓' : 'count_ge(τ) ? k' }}
      </text>
      <line :x1="AX0" :y1="axY" :x2="AX1" :y2="axY" class="axis" />
      <line :x1="xLo" :y1="axY" :x2="xHi" :y2="axY" class="bracket" :class="bisectSt" />
      <line :x1="xLo" :y1="axY - 5" :x2="xLo" :y2="axY + 5" class="tick" :class="bisectSt" />
      <line :x1="xHi" :y1="axY - 5" :x2="xHi" :y2="axY + 5" class="tick" :class="bisectSt" />
      <line :x1="xTau" :y1="axY - 8" :x2="xTau" :y2="axY + 8" class="tau" :class="bisectSt" />
      <text :x="xLo" :y="axY + 17" class="axend">lo</text>
      <text :x="xHi" :y="axY + 17" class="axend">hi</text>
      <text :x="xTau" :y="axY - 11" class="axend tauL" :class="bisectSt">τ</text>
    </svg>

    <p class="cap">
      二分阈值法是一条<b>大批量、强 lockstep</b> 的流水：<b>B 行</b>各由一个 warp 处理，整行载入 register（<b>vals[L]</b>），
      求出初始区间后<b>二分一个阈值 τ</b>——每轮 <b>count_ge(τ)</b> 用 ballot + popc 数出过线者、与 k 比较来收窄 [lo,hi]，
      所有 warp 轮数一致、<b>无尾部发散</b>。收敛后用 ballot 前缀和紧凑收集 top-k。全程无 shared、无 atomic，
      显存流量每行仅 R 读 + k 写，且<b>与 k 无关</b>——这是它在海量 row-wise 小输入下吞吐极高的根本。
    </p>
  </div>
</template>

<style scoped>
.demo {
  max-width: 660px; margin: 0 auto;
  font-family: -apple-system, 'PingFang SC', sans-serif; color: #1f2329;
}
.bar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; flex-wrap: wrap; gap: 6px; }
.btns button {
  font: inherit; cursor: pointer; background: #fff; color: #1f2329;
  border: 1px solid #d9dee5; border-radius: 6px; padding: 5px 12px; font-size: 0.85rem; margin-right: 6px;
}
.btns button:hover:not(:disabled) { border-color: #1677ff; color: #1677ff; }
.btns button:disabled { opacity: 0.45; cursor: not-allowed; }
.status { font-size: 0.82rem; color: #8a909c; }
.status.ok { color: #2f8f6b; }
.params {
  font-size: 0.76rem; color: #8a909c; background: #f2f5fa;
  border: 1px solid #e7ecf3; border-radius: 6px; padding: 4px 10px; margin-bottom: 8px;
}
.params b { color: #1677ff; font-weight: 600; }
.stage { width: 100%; background: #f7f9fc; border: 1px solid #e7ecf3; border-radius: 10px; }

.rowcard { fill: #eef1f6; stroke: #d3dae4; stroke-width: 1.2; }
.rowcard.front { fill: #e8f1ff; stroke: #1677ff; }

.node { fill: #eef1f6; stroke: #d3dae4; stroke-width: 1.4; transition: all 0.3s; }
.node.active { fill: #e8f1ff; stroke: #1677ff; }
.node.done { fill: #eaf4ef; stroke: #86c4a6; }

.ntxt { text-anchor: middle; font-size: 11.5px; font-weight: 600; fill: #aab2bf; transition: fill 0.3s; }
.ntxt.active { fill: #1677ff; }
.ntxt.done { fill: #2f8f6b; }
.ntxt.front { fill: #1677ff; }
.tag { text-anchor: middle; font-size: 9.5px; fill: #c2c9d4; transition: fill 0.3s; }
.tag.active { fill: #5a86c4; }
.tag.done { fill: #5fa888; }
.tag.on { fill: #8a909c; }

.arrow { stroke: #dfe4ec; stroke-width: 1.6; fill: none; transition: stroke 0.3s; }
.arrow.active { stroke: #1677ff; stroke-dasharray: 5 4; animation: th-flow 0.7s linear infinite; }
.arrow.done { stroke: #aedcc6; }
.loop { stroke: #dfe4ec; stroke-width: 1.6; fill: none; transition: stroke 0.3s; }
.loop.active { stroke: #1677ff; stroke-dasharray: 4 3; animation: th-flow 0.6s linear infinite; }
.loop.done { stroke: #aedcc6; }
marker path { fill: #cbd2dc; }

.op { text-anchor: middle; font-size: 10px; fill: #c2c9d4; font-weight: 600; transition: fill 0.3s; }
.op.active { fill: #1677ff; }
.op.done { fill: #5fa888; }

.axis { stroke: #d3dae4; stroke-width: 1.4; }
.bracket { stroke: #c7d6ec; stroke-width: 5; stroke-linecap: round; transition: all 0.4s; }
.bracket.active { stroke: #1677ff; }
.bracket.done { stroke: #86c4a6; }
.tick { stroke: #b3bac6; stroke-width: 1.6; transition: stroke 0.3s; }
.tick.active { stroke: #1677ff; }
.tick.done { stroke: #2f8f6b; }
.tau { stroke: #b3bac6; stroke-width: 2; transition: stroke 0.3s; }
.tau.active { stroke: #d9785f; }
.tau.done { stroke: #2f8f6b; }
.axlbl { text-anchor: middle; font-size: 10px; fill: #aab2bf; transition: fill 0.3s; }
.axlbl.active { fill: #1677ff; }
.axlbl.done { fill: #2f8f6b; }
.axend { text-anchor: middle; font-size: 9.5px; fill: #8a909c; }
.axend.tauL { fill: #b3bac6; }
.axend.tauL.active { fill: #d9785f; }
.axend.tauL.done { fill: #2f8f6b; }

@keyframes th-flow { to { stroke-dashoffset: -9; } }

.cap { font-size: 0.85rem; line-height: 1.7; color: #5c6370; margin-top: 10px; }
.cap b { color: #1677ff; }
</style>
