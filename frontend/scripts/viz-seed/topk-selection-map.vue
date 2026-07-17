<!-- viz-name: TopK 自适应选型：成本-k 包络（Bitonic vs Radix） -->
<script setup>
import { ref, computed } from 'vue'

// 示意图：固定 n 下相对成本随 k 的变化。Bitonic 随 k 上升、并在寄存器墙处不可行；
// Radix 对 k 近似平坦。下包络（更低=更快）即所选算法，交叉点 k* 随 n 由 Factor(n) 右移。
const Lmax = 12 // k 轴到 2^12 = 4096
const Lwall = Math.log2(3680) // Bitonic 寄存器硬墙
const clamp = (x, a, b) => Math.max(a, Math.min(b, x))

// Bitonic 相对成本：随 log2(k) 凸增（O(k log²k)+寄存器压力）
const costB = (k) => clamp(0.1 + 0.62 * Math.pow(Math.log2(k) / Lwall, 2.2), 0, 1)
// 交叉点 k*(n)：过两锚点 n=8192→195、n=131072→878 拟合（k* ∝ n^0.543）
const kstar = (n) => clamp(195 * Math.pow(n / 8192, 0.543), 2, 3680)

const log2n = ref(15) // n = 2^15
const log2k = ref(7) // k 标记 = 2^7
const n = computed(() => Math.pow(2, log2n.value))
const kMark = computed(() => Math.round(Math.pow(2, log2k.value)))
const kStarV = computed(() => Math.round(kstar(n.value)))
const radixLevel = computed(() => costB(kStarV.value)) // 平线高度=交叉点处成本
const pickBitonic = computed(() => kMark.value < kStarV.value)

// 绘图坐标
const W = 600,
  H = 244,
  mL = 46,
  mR = 16,
  mT = 16,
  mB = 36
const pW = W - mL - mR,
  pH = H - mT - mB
const xOf = (L) => mL + (clamp(L, 0, Lmax) / Lmax) * pW
const yOf = (c) => mT + (1 - clamp(c, 0, 1)) * pH
const xk = (k) => xOf(Math.log2(k))

const bitonicPath = computed(() => {
  let d = ''
  for (let L = 0; L <= Lmax + 0.001; L += 0.25) {
    const x = xOf(L),
      y = yOf(costB(Math.pow(2, L)))
    d += (d ? ' L ' : 'M ') + x.toFixed(1) + ' ' + y.toFixed(1)
  }
  return d
})
const yRadix = computed(() => yOf(radixLevel.value))
const xStar = computed(() => xk(kStarV.value))
const xWall = computed(() => xOf(Lwall))
const xMark = computed(() => xk(kMark.value))
const yMark = computed(() => yOf(pickBitonic.value ? costB(kMark.value) : radixLevel.value))

const ticks = [1, 8, 64, 256, 1024, 4096]
const fmt = (v) => (v >= 1024 ? v / 1024 + 'K' : '' + v)
</script>

<template>
  <div class="demo">
    <div class="ctrls">
      <label
        >n = <b>{{ fmt(n) }}</b
        >（2^{{ log2n }}）
        <input type="range" min="13" max="19" step="1" v-model.number="log2n" />
      </label>
      <label
        >k = <b>{{ kMark }}</b>
        <input type="range" min="0" max="12" step="0.25" v-model.number="log2k" />
      </label>
    </div>
    <div class="readout">
      交叉点 <b>k* ≈ {{ kStarV }}</b
      >（由 Factor(n) 定）· 当前 <b>k={{ kMark }}</b> →
      <span :class="pickBitonic ? 'pb' : 'pr'">{{
        pickBitonic ? '选 Bitonic（register 内、无 atomic）' : '选 Radix（成本与 k 无关）'
      }}</span>
    </div>

    <svg :viewBox="`0 0 ${W} ${H}`" class="stage">
      <!-- 选择带：k<k* Bitonic 区、k≥k* Radix 区 -->
      <rect :x="mL" :y="mT" :width="xStar - mL" :height="pH" class="band bit" />
      <rect :x="xStar" :y="mT" :width="W - mR - xStar" :height="pH" class="band rdx" />
      <!-- 寄存器墙右侧：Bitonic 不可行 -->
      <rect :x="xWall" :y="mT" :width="W - mR - xWall" :height="pH" class="band wall" />

      <!-- 坐标轴 -->
      <line :x1="mL" :y1="mT" :x2="mL" :y2="mT + pH" class="axis" />
      <line :x1="mL" :y1="mT + pH" :x2="W - mR" :y2="mT + pH" class="axis" />
      <text :x="mL - 4" :y="mT + 8" class="ylbl">高</text>
      <text :x="mL - 4" :y="mT + pH" class="ylbl">低</text>
      <text :x="mL - 40" :y="mT + pH / 2" class="yaxis">相对成本</text>
      <g v-for="t in ticks" :key="'tk' + t">
        <line :x1="xk(t)" :y1="mT + pH" :x2="xk(t)" :y2="mT + pH + 4" class="axis" />
        <text :x="xk(t)" :y="mT + pH + 16" class="xtk">{{ fmt(t) }}</text>
      </g>
      <text :x="W - mR" :y="mT + pH + 16" class="xaxis">k（对数）</text>

      <!-- Radix 平线 -->
      <line :x1="mL" :y1="yRadix" :x2="W - mR" :y2="yRadix" class="curve rdx" />
      <text :x="W - mR - 4" :y="yRadix - 5" class="clbl rdx">Radix（对 k 平坦）</text>
      <!-- Bitonic 曲线 -->
      <path :d="bitonicPath" class="curve bit" />
      <text :x="xk(40)" :y="yOf(costB(40)) - 6" class="clbl bit">Bitonic</text>

      <!-- 寄存器墙 -->
      <line :x1="xWall" :y1="mT" :x2="xWall" :y2="mT + pH" class="wallline" />
      <text :x="xWall - 3" :y="mT + 11" class="walllbl">寄存器墙 ~3680</text>

      <!-- 交叉点 -->
      <line :x1="xStar" :y1="mT" :x2="xStar" :y2="mT + pH" class="starline" />
      <circle :cx="xStar" :cy="yRadix" r="3.5" class="stardot" />
      <text :x="xStar" :y="mT + pH - 6" class="starlbl">k* {{ kStarV }}</text>

      <!-- k 标记 -->
      <line
        :x1="xMark"
        :y1="mT"
        :x2="xMark"
        :y2="mT + pH"
        class="markline"
        :class="{ pb: pickBitonic }"
      />
      <circle :cx="xMark" :cy="yMark" r="4" class="markdot" :class="{ pb: pickBitonic }" />
    </svg>

    <p class="cap">
      固定 n 下，相对成本随 k 的变化（示意）：<b>Bitonic</b> 随 k 凸增、并在<b>寄存器墙</b>（A100 约
      k=3680）后溢出而不可行； <b>Radix</b> 片上只一个 histogram、对 k
      <b>近似平坦</b>。两线下包络（更低=更快）即应选算法—— 小 k 选 Bitonic，越过交叉点
      <b>k*</b> 后选 Radix。拖 n 滑杆：n 越大，Radix 的固定开销越被摊薄、k* 越往右（n=8K 时
      k*≈195、128K 时 k*≈878）， Bitonic 的优势区随之变宽。这条按 k 取下包络的策略，正是消除
      performance cliff 的自适应选型。 （另：海量短向量是正交分支，按<b>输入形态</b>而非 k
      选「二分阈值」。）
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
.ctrls {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.ctrls label {
  font-size: 0.8rem;
  color: #5c6370;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ctrls b {
  color: #1677ff;
}
.ctrls input[type='range'] {
  width: 200px;
  accent-color: #1677ff;
  cursor: pointer;
}
.readout {
  font-size: 0.8rem;
  color: #5c6370;
  background: #f2f5fa;
  border: 1px solid #e7ecf3;
  border-radius: 6px;
  padding: 4px 10px;
  margin-bottom: 8px;
}
.readout b {
  color: #1f2329;
}
.readout .pb {
  color: #1677ff;
  font-weight: 600;
}
.readout .pr {
  color: #2f8f6b;
  font-weight: 600;
}
.stage {
  width: 100%;
  background: #f7f9fc;
  border: 1px solid #e7ecf3;
  border-radius: 10px;
}

.band {
  opacity: 0.5;
}
.band.bit {
  fill: #eef4ff;
}
.band.rdx {
  fill: #eef6f1;
}
.band.wall {
  fill: #fdeeea;
  opacity: 0.7;
}
.axis {
  stroke: #c8d0db;
  stroke-width: 1;
}
.ylbl {
  font-size: 9px;
  fill: #aab2bf;
  text-anchor: end;
}
.yaxis {
  font-size: 10px;
  fill: #8a909c;
  text-anchor: middle;
  transform-box: fill-box;
  transform-origin: center;
  transform: rotate(-90deg);
}
.xaxis {
  font-size: 10px;
  fill: #8a909c;
  text-anchor: end;
}
.xtk {
  font-size: 9.5px;
  fill: #8a909c;
  text-anchor: middle;
}

.curve {
  fill: none;
  stroke-width: 2.4;
}
.curve.bit {
  stroke: #1677ff;
}
.curve.rdx {
  stroke: #2f8f6b;
  stroke-dasharray: 6 3;
}
.clbl {
  font-size: 10.5px;
  font-weight: 600;
}
.clbl.bit {
  fill: #1677ff;
  text-anchor: start;
}
.clbl.rdx {
  fill: #2f8f6b;
  text-anchor: end;
}

.wallline {
  stroke: #d9785f;
  stroke-width: 1.2;
  stroke-dasharray: 3 3;
}
.walllbl {
  font-size: 9px;
  fill: #c4664f;
  text-anchor: end;
}
.starline {
  stroke: #8a909c;
  stroke-width: 1.2;
  stroke-dasharray: 4 3;
}
.stardot {
  fill: #1f2329;
}
.starlbl {
  font-size: 10px;
  fill: #1f2329;
  font-weight: 600;
  text-anchor: middle;
}

.markline {
  stroke: #2f8f6b;
  stroke-width: 1.6;
}
.markline.pb {
  stroke: #1677ff;
}
.markdot {
  fill: #2f8f6b;
  stroke: #fff;
  stroke-width: 1.5;
}
.markdot.pb {
  fill: #1677ff;
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
