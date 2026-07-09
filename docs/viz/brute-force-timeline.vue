<script setup>
import { ref, computed } from 'vue'

// 真实数据：服务器认证日志里每天的「Failed password」次数（连续 20 天，横轴用相对天数）
const data = [
  { d: '第1天', v: 5 }, { d: '第2天', v: 6 }, { d: '第3天', v: 28 }, { d: '第4天', v: 31 },
  { d: '第5天', v: 29 }, { d: '第6天', v: 25 }, { d: '第7天', v: 20 }, { d: '第8天', v: 2258 },
  { d: '第9天', v: 30 }, { d: '第10天', v: 14 }, { d: '第11天', v: 14 }, { d: '第12天', v: 2243 },
  { d: '第13天', v: 10 }, { d: '第14天', v: 32 }, { d: '第15天', v: 1838 }, { d: '第16天', v: 18 },
  { d: '第17天', v: 2 }, { d: '第18天', v: 2241 }, { d: '第19天', v: 34 }, { d: '第20天', v: 4 },
]
const total = data.reduce((s, x) => s + x.v, 0)
const SPIKE = 500 // 超过此值算「爆破尖峰」，染红

// 布局
const W = 780
const H = 300
const padL = 30
const padR = 14
const padT = 16
const padB = 34
const innerW = W - padL - padR
const innerH = H - padT - padB
const slot = innerW / data.length
const barW = slot * 0.62

const sqrtScale = ref(true)
const maxV = Math.max(...data.map((x) => x.v))
function scaled(v) {
  return sqrtScale.value ? Math.sqrt(v) / Math.sqrt(maxV) : v / maxV
}
function barH(v) {
  return Math.max(2, scaled(v) * innerH)
}
function barX(i) {
  return padL + i * slot + (slot - barW) / 2
}
function barY(v) {
  return padT + innerH - barH(v)
}

const hover = ref(-1)
const tip = computed(() => (hover.value >= 0 ? data[hover.value] : null))
</script>

<template>
  <div class="bf">
    <div class="head">
      <div class="stat">
        <span class="big">{{ total.toLocaleString() }}</span>
        <span class="unit">次登录失败 / 20 天</span>
      </div>
      <button class="scale-btn" @click="sqrtScale = !sqrtScale">
        刻度：{{ sqrtScale ? '平方根' : '线性' }}
      </button>
    </div>

    <svg :viewBox="`0 0 ${W} ${H}`" class="canvas" @mouseleave="hover = -1">
      <!-- 基线 -->
      <line :x1="padL" :y1="padT + innerH" :x2="W - padR" :y2="padT + innerH" class="axis" />

      <!-- 柱 -->
      <g v-for="(x, i) in data" :key="i">
        <rect
          :x="barX(i)"
          :y="barY(x.v)"
          :width="barW"
          :height="barH(x.v)"
          rx="2"
          class="bar"
          :class="{ spike: x.v >= SPIKE, hot: hover === i }"
          @mouseenter="hover = i"
        />
        <text
          v-if="i % 2 === 0"
          :x="barX(i) + barW / 2"
          :y="H - 12"
          class="x-label"
        >
          {{ x.d }}
        </text>
      </g>

      <!-- 悬浮读数 -->
      <g v-if="tip">
        <text
          :x="Math.min(Math.max(barX(hover) + barW / 2, 40), W - 40)"
          :y="barY(tip.v) - 8"
          class="tip"
          :class="{ spike: tip.v >= SPIKE }"
        >
          {{ tip.d }}：{{ tip.v.toLocaleString() }} 次
        </text>
      </g>
    </svg>

    <p class="cap">
      鼠标悬到柱子上看当天次数。红色是爆破尖峰（单日超 500），日常只有几十次。
      平方根刻度是为了让「几十次」和「两千多次」能挤在同一张图里都看得见——线性刻度下，日常那几根几乎贴地。
    </p>
  </div>
</template>

<style scoped>
.bf {
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #1f2328;
}
.head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 8px;
}
.stat {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.big {
  font-size: 1.9rem;
  font-weight: 700;
  color: #cf1322;
  letter-spacing: 0.5px;
}
.unit {
  font-size: 0.85rem;
  color: #8a9099;
}
.scale-btn {
  border: 1px solid #d0d7de;
  background: #fff;
  color: #57606a;
  border-radius: 7px;
  padding: 5px 12px;
  font-size: 0.82rem;
  cursor: pointer;
}
.scale-btn:hover {
  border-color: #1677ff;
  color: #1677ff;
}
.canvas {
  width: 100%;
  height: auto;
  display: block;
}
.axis {
  stroke: #d8dee4;
  stroke-width: 1;
}
.bar {
  fill: #b6c2cd;
  transition: fill 0.15s;
  cursor: pointer;
}
.bar.spike {
  fill: #f0838c;
}
.bar.hot {
  fill: #1677ff;
}
.bar.spike.hot {
  fill: #cf1322;
}
.x-label {
  text-anchor: middle;
  font-size: 10px;
  fill: #9aa0a6;
}
.tip {
  text-anchor: middle;
  font-size: 12px;
  font-weight: 600;
  fill: #1677ff;
}
.tip.spike {
  fill: #cf1322;
}
.cap {
  margin: 10px 0 0;
  font-size: 0.82rem;
  line-height: 1.65;
  color: #8a9099;
}
</style>
