<!-- viz-name: 二分阈值收敛（一 warp 一条向量） -->
<script setup>
import { ref, computed } from 'vue'

// 一条短向量，二分一个阈值 τ 使「≥τ 的元素恰好 k 个」。GPU 上用 ballot+popcount 无分支计数。
const N = 18
const DMIN = 0, DMAX = 100

function distinctVals(n) {
  const set = new Set()
  while (set.size < n) set.add(2 + Math.floor(Math.random() * 97))
  return [...set]
}
const vals = ref(distinctVals(N))
const k = ref(5)
const earlyStop = ref(false)
const MAXITER = 6 // early-stop 固定轮数

const st = ref(null)
function reset() {
  st.value = { lo: DMIN, hi: DMAX, tau: (DMIN + DMAX) / 2, iter: 0, finished: false }
}
reset()

const countGE = (tau) => vals.value.reduce((a, v) => a + (v >= tau ? 1 : 0), 0)
const curCount = computed(() => countGE(st.value.tau))

function next() {
  const s = st.value
  if (s.finished) return
  const c = countGE(s.tau)
  s.iter += 1
  if (!earlyStop.value && c === k.value) { s.finished = true; return }
  if (c > k.value) s.lo = s.tau // 选中太多 → 抬高阈值
  else s.hi = s.tau // 选中太少 → 降低阈值
  s.tau = (s.lo + s.hi) / 2
  if (earlyStop.value && s.iter >= MAXITER) { s.finished = true }
  if (!earlyStop.value && s.iter >= 24) s.finished = true
}
function shuffleData() { vals.value = distinctVals(N); reset() }
function onParam() { reset() }

const err = computed(() => Math.abs(curCount.value - k.value))
const selected = (v) => v >= st.value.tau

// 布局
const padX = 36, plotW = 488, axisY = 96, W = padX * 2 + plotW
const xOf = (v) => padX + ((v - DMIN) / (DMAX - DMIN)) * plotW
const yOf = (i) => axisY - 34 + (i % 4) * 17 // 轻微错开避免重叠
</script>

<template>
  <div class="demo">
    <div class="bar">
      <div class="btns">
        <button @click="next" :disabled="st.finished">步进 ▶</button>
        <button @click="reset">重置</button>
        <button @click="shuffleData">随机数据</button>
        <label class="kctl">k = {{ k }}
          <input type="range" min="1" max="12" v-model.number="k" @input="onParam" />
        </label>
        <label class="chk">
          <input type="checkbox" v-model="earlyStop" @change="onParam" /> early-stop（{{ MAXITER }} 轮近似）
        </label>
      </div>
    </div>

    <div class="metrics">
      迭代 <b>{{ st.iter }}</b><span class="sep">·</span>
      阈值 τ <b>{{ st.tau.toFixed(1) }}</b><span class="sep">·</span>
      选中 <b :class="{ bad: curCount !== k }">{{ curCount }}</b> / 目标 {{ k }}
      <span class="sep">·</span>
      <span v-if="st.finished && err === 0" class="ok">命中精确 top-{{ k }}</span>
      <span v-else-if="st.finished" class="warn">近似停手，误差 {{ err }} 个</span>
      <span v-else class="hint">{{ curCount > k ? '选中过多 → 抬高 τ' : curCount < k ? '选中过少 → 降低 τ' : '计数已等于 k' }}</span>
    </div>

    <svg :viewBox="`0 0 ${W} 150`" class="stage">
      <!-- lo/hi 当前搜索区间 -->
      <rect :x="xOf(st.lo)" :y="20" :width="xOf(st.hi) - xOf(st.lo)" height="108" class="bracket" />
      <!-- 数轴 -->
      <line :x1="padX" :x2="padX + plotW" :y1="axisY + 26" :y2="axisY + 26" class="axis" />
      <text :x="padX" :y="axisY + 44" class="ax-lbl">小</text>
      <text :x="padX + plotW" :y="axisY + 44" class="ax-lbl end">大 →（值）</text>

      <!-- 元素点：≥τ 选中(蓝)，否则灰 -->
      <g v-for="(v, i) in vals" :key="i" class="dot" :style="{ transform: `translate(${xOf(v)}px, ${yOf(i)}px)` }">
        <circle r="6" :class="{ sel: selected(v) }" />
      </g>

      <!-- 阈值线 -->
      <g class="tau" :style="{ transform: `translateX(${xOf(st.tau)}px)` }">
        <line :x1="0" :x2="0" :y1="16" :y2="axisY + 30" />
        <polygon points="-5,10 5,10 0,18" />
        <text x="0" y="6" class="tau-lbl">τ</text>
      </g>
    </svg>

    <p class="cap">
      不找具体的第 k 大元素，而是<b>二分一个阈值</b> τ，使「≥τ 的个数恰好 k」。warp 内用
      <b>ballot + popcount</b> 一条指令数出有多少元素过线、无分支。开 <b>early-stop</b> 固定轮数提前停手得到近似解
      （误差通常 ≤5%）——稀疏化训练里用这点精度换数倍速度非常划算。
    </p>
  </div>
</template>

<style scoped>
.demo {
  max-width: 640px; margin: 0 auto;
  font-family: -apple-system, 'PingFang SC', sans-serif; color: #1f2329;
}
.bar { margin-bottom: 8px; }
.btns { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.btns button {
  font: inherit; cursor: pointer; background: #fff; color: #1f2329;
  border: 1px solid #d9dee5; border-radius: 6px; padding: 5px 12px; font-size: 0.85rem;
}
.btns button:hover { border-color: #1677ff; color: #1677ff; }
.btns button:disabled { opacity: 0.45; cursor: not-allowed; color: #1f2329; border-color: #d9dee5; }
.kctl, .chk { font-size: 0.82rem; color: #5c6370; display: inline-flex; align-items: center; gap: 5px; }
.metrics { font-size: 0.85rem; color: #8a909c; margin-bottom: 8px; }
.metrics b { color: #1677ff; font-size: 1.0rem; }
.metrics b.bad { color: #d9785f; }
.metrics .sep { margin: 0 8px; }
.metrics .ok { color: #2f8f6b; }
.metrics .warn { color: #c98a3c; }
.metrics .hint { color: #8a909c; }
.stage { width: 100%; background: #f7f9fc; border: 1px solid #e7ecf3; border-radius: 10px; }
.bracket { fill: rgba(22, 119, 255, 0.06); transition: x 0.5s, width 0.5s; }
.axis { stroke: #cbd2dc; stroke-width: 1.5; }
.ax-lbl { font-size: 11px; fill: #8a909c; }
.ax-lbl.end { text-anchor: end; }
.dot { transition: transform 0.4s; }
.dot circle { fill: #dce1ea; stroke: #c4ccd8; stroke-width: 1; transition: fill 0.4s, stroke 0.4s; }
.dot circle.sel { fill: #1677ff; stroke: #0958d9; }
.tau { transition: transform 0.5s cubic-bezier(0.4, 0, 0.2, 1); }
.tau line { stroke: #d9785f; stroke-width: 2; stroke-dasharray: 4 3; }
.tau polygon { fill: #d9785f; }
.tau-lbl { text-anchor: middle; font-size: 12px; font-weight: 700; fill: #c45c43; }
.cap { font-size: 0.85rem; line-height: 1.7; color: #5c6370; margin-top: 10px; }
.cap b { color: #1677ff; }
</style>
