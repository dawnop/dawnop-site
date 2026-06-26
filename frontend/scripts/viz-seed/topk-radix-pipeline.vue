<!-- viz-name: Radix Select 多趟缩并总览（histogram → pivot → filter） -->
<script setup>
import { ref, computed } from 'vue'

// 串联多趟流水：候选集 → histogram → top-down 扫描定 pivot → filter 缩并 → 回到候选集（÷256/趟）；
// 比特用尽后 collect 扫原始数组取 top-k。逐趟点开，候选条随趟缩短、prefix 逐趟锁定 8 bit。
const D = 8
const PASSES = 4 // ⌈32/D⌉
const W = 640

const yBox = 70, boxH = 52, boxT = yBox - boxH / 2, boxB = yBox + boxH / 2
const cyc = [
  { id: 'cand', x: 82, label: '候选集', tag: '' },
  { id: 'hist', x: 234, label: 'Histogram', tag: 'hierarchical atomics' },
  { id: 'scan', x: 386, label: '扫描定 pivot', tag: '' },
  { id: 'filter', x: 538, label: 'Filter 缩并', tag: 'write buffer' },
]
const cW = 118
const yC = 196, cH = 46, cT = yC - cH / 2
const collect = { x: 232, label: 'Collect' }
const topk = { x: 430, label: 'top-k' }

const step = ref(0)
const next = () => { if (step.value < PASSES) step.value++ }
const reset = () => { step.value = 0 }
const done = computed(() => step.value >= PASSES)

// 循环段（cand/hist/scan/filter + loop 弧）状态
const cycSt = computed(() => (step.value === 0 ? 'idle' : step.value < PASSES ? 'active' : 'done'))
// 收尾段（collect/top-k）状态
const outSt = computed(() => (step.value < PASSES ? 'idle' : 'active'))

const candFrac = computed(() => Math.pow(0.5, Math.min(step.value, PASSES - 1)))
const candLabel = ['n', 'n/256', 'n/256²', 'n/256³', '≈ top-k'][Math.min(step.value, 4)]
const lockedBits = computed(() => Math.min(D * step.value, 32))

const arrow = (x1, x2) => `M ${x1} ${yBox} L ${x2} ${yBox}`
const caption = computed(() => {
  if (step.value === 0) return '起点：候选集 = 全部 n 个 ordered key（已 f2u_order）'
  if (step.value < PASSES)
    return `第 ${step.value} 趟：histogram → 定 pivot → filter 缩并，候选 ÷256，已锁定 ${lockedBits.value} bit`
  return '比特用尽：prefix 即第 k 大的阈值 → collect 扫原始数组得 top-k'
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
      示例参数：<b>D=8</b> → bins=<b>256</b> · 每趟候选 <b>÷256</b> · passes ≤ ⌈32/8⌉ = <b>{{ PASSES }}</b> · prefix 每趟锁定 8 bit
    </div>

    <svg :viewBox="`0 0 ${W} 248`" class="stage">
      <defs>
        <marker id="rx-arrow" markerWidth="7" markerHeight="7" refX="4.5" refY="3" orient="auto">
          <path d="M0,0 L5,3 L0,6 Z" />
        </marker>
      </defs>

      <!-- 循环段连接箭头 -->
      <path v-for="i in [0, 1, 2]" :key="'ca' + i"
        :d="arrow(cyc[i].x + cW / 2, cyc[i + 1].x - cW / 2)"
        class="arrow" :class="cycSt" marker-end="url(#rx-arrow)" />

      <!-- loop 弧：filter → 候选集（下一趟，候选 ÷256） -->
      <path
        :d="`M ${cyc[3].x} ${boxB} C ${cyc[3].x - 40} ${boxB + 42}, ${cyc[0].x + 40} ${boxB + 42}, ${cyc[0].x} ${boxB + 4}`"
        class="loop" :class="cycSt" marker-end="url(#rx-arrow)" />
      <text :x="(cyc[0].x + cyc[3].x) / 2" :y="boxB + 50" class="op" :class="cycSt">下一趟：候选 ÷256，prefix 多锁 8 bit</text>

      <!-- 循环段方框 -->
      <g v-for="b in cyc" :key="b.id">
        <rect :x="b.x - cW / 2" :y="boxT" :width="cW" :height="boxH" rx="8" class="node" :class="cycSt" />
        <text :x="b.x" :y="b.tag ? yBox - 2 : yBox + 4" class="ntxt" :class="cycSt">{{ b.label }}</text>
        <text v-if="b.tag" :x="b.x" :y="yBox + 13" class="tag" :class="cycSt">{{ b.tag }}</text>
        <!-- 候选集方框内的缩并条 -->
        <rect v-if="b.id === 'cand'"
          :x="b.x - cW / 2 + 10" :y="boxB - 14" :width="(cW - 20) * candFrac" height="6" rx="3"
          class="candbar" :class="cycSt" />
        <text v-if="b.id === 'cand'" :x="b.x" :y="boxB - 18" class="cnt" :class="cycSt">{{ candLabel }}</text>
      </g>

      <!-- 收尾：filter → collect → top-k -->
      <path :d="`M ${cyc[3].x} ${boxB} C ${cyc[3].x} ${cT - 30}, ${collect.x + 120} ${cT - 6}, ${collect.x + cW / 2} ${yC}`"
        class="arrow exit" :class="outSt" marker-end="url(#rx-arrow)" />
      <path :d="`M ${collect.x + cW / 2} ${yC} L ${topk.x - cW / 2} ${yC}`"
        class="arrow" :class="outSt" marker-end="url(#rx-arrow)" />
      <g>
        <rect :x="collect.x - cW / 2" :y="cT" :width="cW" :height="cH" rx="8" class="node" :class="outSt" />
        <text :x="collect.x" :y="yC - 2" class="ntxt" :class="outSt">Collect</text>
        <text :x="collect.x" :y="yC + 12" class="tag" :class="outSt">扫原始数组 ≥ 阈值</text>
      </g>
      <g>
        <rect :x="topk.x - cW / 2" :y="cT" :width="cW" :height="cH" rx="8" class="node final" :class="outSt" />
        <text :x="topk.x" :y="yC + 4" class="ntxt big" :class="outSt">top-k</text>
      </g>
    </svg>

    <p class="cap">
      radix select 是一条<b>多趟缩并</b>流水：每趟对当前候选集按 8 bit 统计 <b>histogram</b>，从最高 bin 向下累加定位第 k 大所在的 <b>pivot bin</b>，
      再用 <b>filter</b> 把落在 pivot bin 的候选紧凑搬到下一趟——候选规模随之 <b>÷256</b>，prefix 每趟锁定 8 bit。至多 4 趟比特用尽，
      <b>collect</b> 扫原始数组取出所有不小于阈值的元素即 top-k。片上只一个 256-bin histogram、<b>与 k 无关</b>，故能扛大 k 与任意分位数。
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
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 6px; flex-wrap: wrap; gap: 6px;
}
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

.node { fill: #eef1f6; stroke: #d3dae4; stroke-width: 1.4; transition: all 0.3s; }
.node.active { fill: #e8f1ff; stroke: #1677ff; }
.node.done { fill: #eaf4ef; stroke: #86c4a6; }
.node.final.active { fill: #e6f7ef; stroke: #2f8f6b; stroke-width: 2; }

.ntxt { text-anchor: middle; font-size: 12px; font-weight: 600; fill: #aab2bf; transition: fill 0.3s; }
.ntxt.active { fill: #1677ff; }
.ntxt.done { fill: #2f8f6b; }
.ntxt.big { font-size: 13px; }
.tag { text-anchor: middle; font-size: 9.5px; fill: #c2c9d4; transition: fill 0.3s; }
.tag.active { fill: #5a86c4; }
.tag.done { fill: #5fa888; }

.candbar { fill: #c7d6ec; transition: all 0.4s; }
.candbar.active { fill: #1677ff; }
.candbar.done { fill: #86c4a6; }
.cnt { text-anchor: middle; font-size: 10px; fill: #aab2bf; transition: fill 0.3s; }
.cnt.active { fill: #1677ff; }
.cnt.done { fill: #2f8f6b; }

.arrow { stroke: #dfe4ec; stroke-width: 1.6; fill: none; transition: stroke 0.3s; }
.arrow.active { stroke: #1677ff; stroke-dasharray: 5 4; animation: rx-flow 0.7s linear infinite; }
.arrow.done { stroke: #aedcc6; }
.loop { stroke: #dfe4ec; stroke-width: 1.6; fill: none; transition: stroke 0.3s; }
.loop.active { stroke: #1677ff; stroke-dasharray: 4 3; animation: rx-flow 0.6s linear infinite; }
.loop.done { stroke: #aedcc6; }
marker path { fill: #cbd2dc; }

.op { text-anchor: middle; font-size: 10px; fill: #c2c9d4; font-weight: 600; transition: fill 0.3s; }
.op.active { fill: #1677ff; }
.op.done { fill: #5fa888; }

@keyframes rx-flow { to { stroke-dashoffset: -9; } }

.cap { font-size: 0.85rem; line-height: 1.7; color: #5c6370; margin-top: 10px; }
.cap b { color: #1677ff; }
</style>
