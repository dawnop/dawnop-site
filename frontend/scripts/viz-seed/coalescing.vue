<!-- viz-name: 访存合并（内存事务） -->
<script setup>
import { ref, computed } from 'vue'

// 一个 warp 的 8 个线程访问显存：合并访问(落在 1 个事务) vs 跨步访问(散到 4 个事务)。
const W = 8
const MEM = 32
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

// 每个线程一个色相，便于把线程和它命中的显存单元对应起来
const hue = (i) => Math.round((i / (W - 1)) * 280)
const targetOf = (i) => (mode.value === 'coalesced' ? i : (i * STRIDE) % MEM)

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
          :fill="`hsl(${hue(i)},70%,93%)`"
          :stroke="`hsl(${hue(i)},55%,58%)`"
        />
        <text :x="threadCX(i)" y="37" class="lbl" :fill="`hsl(${hue(i)},45%,35%)`">T{{ i }}</text>
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
        :style="{ fill: c in hitMap ? `hsl(${hue(hitMap[c])},65%,60%)` : '#e9edf3' }"
      />
      <text :x="padX" :y="memY + cellH + 22" class="cap-lbl">显存地址（连续）→</text>

      <!-- 访问标记：随模式切换平滑滑到目标单元（CSS transform 过渡） -->
      <g
        v-for="i in threads"
        :key="'m' + i"
        class="marker"
        :style="{ transform: `translate(${markerX(i)}px, ${memY - 16}px)` }"
      >
        <circle r="5" :fill="`hsl(${hue(i)},65%,52%)`" />
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
  color: #1f2329;
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
  background: #fff;
  color: #1f2329;
  border: 1px solid #d9dee5;
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
  background: rgba(22, 119, 255, 0.1);
  border-color: #1677ff;
  color: #1677ff;
}
.metrics {
  font-size: 0.85rem;
  color: #8a909c;
}
.metrics b {
  color: #1677ff;
  font-size: 1.05rem;
  transition: color 0.4s;
}
.metrics b.bad {
  color: #cf1322;
}
.metrics .sep {
  margin: 0 8px;
}
.stage {
  width: 100%;
  background: #f8fafc;
  border: 1px solid #e2e6ed;
  border-radius: 10px;
}
.lbl {
  text-anchor: middle;
  font-size: 11px;
  font-weight: 600;
}
.cap-lbl {
  font-size: 11px;
  fill: #8a909c;
}
/* CSS transition 把状态变化自动变成动画，无需任何动画库 */
.cell {
  transition: fill 0.45s ease;
}
.marker {
  transition: transform 0.5s cubic-bezier(0.4, 0, 0.2, 1);
}
.seg-box {
  fill: transparent;
  stroke: #cbd2dc;
  stroke-dasharray: 4 4;
  opacity: 0.7;
  transition: opacity 0.45s, stroke 0.45s;
}
.seg-box.active {
  stroke: #1677ff;
  stroke-dasharray: none;
  opacity: 1;
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
