<script setup>
import { ref, computed } from 'vue'

// 最小「可解释动画」示例：纯内联 SVG + ref 状态 + CSS transition，无任何动画库。
// 演示一个 warp 的 8 个线程访问显存：合并访问(落在 1 个事务) vs 跨步访问(散到 4 个事务)。
const W = 8 // 简化为 8 线程
const MEM = 32 // 显存单元
const SEG = 8 // 一个内存事务覆盖的连续单元数 → 共 4 段
const STRIDE = 4
const padX = 30
const cellW = 17
const cellH = 24
const memY = 150

const mode = ref('coalesced') // 'coalesced' | 'strided'

const threads = [...Array(W).keys()]
const cells = [...Array(MEM).keys()]
const segs = [...Array(MEM / SEG).keys()]

const hue = (i) => Math.round((i / (W - 1)) * 280)
const targetOf = (i) => (mode.value === 'coalesced' ? i : (i * STRIDE) % MEM)

// cellIndex -> 命中它的线程号（用于高亮配色）
const hitMap = computed(() => {
  const m = {}
  threads.forEach((i) => (m[targetOf(i)] = i))
  return m
})
const activeSegs = computed(() => new Set(threads.map((i) => Math.floor(targetOf(i) / SEG))))
const transactions = computed(() => activeSegs.value.size)
const util = computed(() => Math.round((W * 100) / (transactions.value * SEG)))

const markerX = (i) => padX + targetOf(i) * cellW + cellW / 2
const threadCX = (i) => padX + (i + 0.5) * ((MEM * cellW) / W)
</script>

<template>
  <div class="demo">
    <div class="bar">
      <div class="seg-btns">
        <button :class="{ on: mode === 'coalesced' }" @click="mode = 'coalesced'">合并访问</button>
        <button :class="{ on: mode === 'strided' }" @click="mode = 'strided'">跨步访问</button>
      </div>
      <div class="metrics">
        内存事务 <b :class="{ bad: transactions > 1 }">{{ transactions }}</b>
        <span class="sep">·</span>
        有效利用率 <b :class="{ bad: util < 100 }">{{ util }}%</b>
      </div>
    </div>

    <svg viewBox="0 0 604 210" class="stage">
      <!-- 线程 -->
      <g v-for="i in threads" :key="'t' + i">
        <rect
          :x="threadCX(i) - 22"
          y="20"
          width="44"
          height="26"
          rx="5"
          :fill="`hsl(${hue(i)},70%,22%)`"
          :stroke="`hsl(${hue(i)},70%,55%)`"
        />
        <text :x="threadCX(i)" y="37" class="lbl">T{{ i }}</text>
      </g>
      <text :x="padX" y="14" class="cap-lbl">一个 warp（简化为 8 线程）</text>

      <!-- 事务分段框 -->
      <rect
        v-for="s in segs"
        :key="'s' + s"
        class="seg-box"
        :class="{ active: activeSegs.has(s) }"
        :x="padX + s * SEG * cellW"
        :y="memY - 6"
        :width="SEG * cellW"
        :height="cellH + 12"
        rx="6"
      />

      <!-- 显存单元 -->
      <rect
        v-for="c in cells"
        :key="'c' + c"
        class="cell"
        :x="padX + c * cellW + 1.5"
        :y="memY"
        :width="cellW - 3"
        :height="cellH"
        rx="2"
        :style="{
          fill: c in hitMap ? `hsl(${hue(hitMap[c])},70%,55%)` : '#1b2336',
        }"
      />
      <text :x="padX" :y="memY + cellH + 22" class="cap-lbl">显存地址（连续）→</text>

      <!-- 访问标记：随模式切换平滑滑到目标单元（CSS transform 过渡） -->
      <g
        v-for="i in threads"
        :key="'m' + i"
        class="marker"
        :style="{ transform: `translate(${markerX(i)}px, ${memY - 16}px)` }"
      >
        <circle r="5" :fill="`hsl(${hue(i)},70%,60%)`" />
      </g>
    </svg>

    <p class="cap">
      同一 warp 的线程访问显存：<b>合并</b> = 落在 1 个事务里、字节 100% 有用；<b>跨步</b> =
      散落到 4 个事务、只有 25% 有用（其余带宽浪费）。点上面两个按钮看切换。
    </p>
  </div>
</template>

<style scoped>
.demo {
  max-width: 680px;
  margin: 0 auto;
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #d7dce5;
}
.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.seg-btns button {
  font: inherit;
  cursor: pointer;
  background: #161b2b;
  color: #d7dce5;
  border: 1px solid #232a3d;
  padding: 6px 14px;
  font-size: 0.85rem;
}
.seg-btns button:first-child {
  border-radius: 6px 0 0 6px;
}
.seg-btns button:last-child {
  border-radius: 0 6px 6px 0;
  border-left: none;
}
.seg-btns button.on {
  background: rgba(118, 185, 0, 0.16);
  border-color: #76b900;
  color: #d9f29b;
}
.metrics {
  font-size: 0.85rem;
  color: #8b93a7;
}
.metrics b {
  color: #76b900;
  font-size: 1.05rem;
  transition: color 0.4s;
}
.metrics b.bad {
  color: #ff7a7a;
}
.metrics .sep {
  margin: 0 8px;
}
.stage {
  width: 100%;
  background: #0f1320;
  border: 1px solid #232a3d;
  border-radius: 10px;
}
.lbl {
  text-anchor: middle;
  font-size: 11px;
  fill: #d7dce5;
}
.cap-lbl {
  font-size: 11px;
  fill: #8b93a7;
}
/* 关键：CSS transition 把状态变化自动变成动画，无需任何动画库 */
.cell {
  transition: fill 0.45s ease;
}
.marker {
  transition: transform 0.5s cubic-bezier(0.4, 0, 0.2, 1);
}
.seg-box {
  fill: transparent;
  stroke: #2a3450;
  stroke-dasharray: 4 4;
  opacity: 0.5;
  transition: opacity 0.45s, stroke 0.45s;
}
.seg-box.active {
  stroke: #76b900;
  stroke-dasharray: none;
  opacity: 0.9;
}
.cap {
  font-size: 0.85rem;
  line-height: 1.7;
  color: #a9b1c2;
  margin-top: 10px;
}
.cap b {
  color: #76b900;
}
</style>
