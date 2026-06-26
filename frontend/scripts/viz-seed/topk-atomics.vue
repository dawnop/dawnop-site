<!-- viz-name: 层级化原子 vs 朴素全局原子 -->
<script setup>
import { ref, computed } from 'vue'

// 一个 block 16 个线程往 4 个 bin 的直方图累加：朴素=每线程一次全局原子；层级=先 shared 聚合，再常数次全局原子。
const T = 16
const B = 4
const mode = ref('naive') // 'naive' | 'hier'

const randBins = () => Array.from({ length: T }, () => Math.floor(Math.random() * B))
const binOf = ref(randBins())
const shuffleData = () => { binOf.value = randBins() }

const binCount = computed(() => {
  const c = Array(B).fill(0)
  for (const b of binOf.value) c[b]++
  return c
})
const globalAtomics = computed(() => (mode.value === 'naive' ? T : B))
const maxContend = computed(() => (mode.value === 'naive' ? Math.max(...binCount.value) : 1))

// 布局
const W = 560
const tY = 34, shY = 116, gY = 188
const tX = (i) => 38 + i * ((W - 76) / (T - 1))
// 淡雅的 4 色分类盘（参考 seaborn muted / AntV）+ 对应浅色填充
const palette = ['#6f9fe0', '#74c2a4', '#e0b074', '#b48fd0']
const paletteSoft = ['#e8effa', '#e6f3ec', '#f7eedd', '#f0e8f6']
const binColor = (b) => palette[b]
const groupW = B * 78 + (B - 1) * 22
const binStart = (W - groupW) / 2
const binX = (b) => binStart + b * (78 + 22) + 39
</script>

<template>
  <div class="demo">
    <div class="bar">
      <div class="seg-btns">
        <button :class="{ on: mode === 'naive' }" @click="mode = 'naive'">朴素全局原子</button>
        <button :class="{ on: mode === 'hier' }" @click="mode = 'hier'">层级聚合</button>
      </div>
      <button class="ghost" @click="shuffleData">随机分配</button>
    </div>

    <div class="metrics">
      全局原子次数 <b :class="{ bad: mode === 'naive' }">{{ globalAtomics }}</b>
      <span class="sep">·</span>
      同一 bin 最大并发争抢 <b :class="{ bad: maxContend > 1 }">{{ maxContend }}</b>
      <span class="sep">·</span>
      <span v-if="mode === 'naive'" class="hint">每个线程都抢全局直方图</span>
      <span v-else class="ok">shared 内先聚合，只剩 {{ B }} 次全局原子</span>
    </div>

    <svg :viewBox="`0 0 ${W} 226`" class="stage">
      <!-- 线程 -->
      <text :x="38" y="16" class="lbl">16 个线程（一个 block）</text>
      <g v-for="i in T" :key="'t' + i">
        <rect :x="tX(i - 1) - 9" :y="tY - 11" width="18" height="22" rx="4"
          :fill="paletteSoft[binOf[i - 1]]" :stroke="binColor(binOf[i - 1])" />
      </g>

      <!-- 朴素：每线程 → 全局 bin（红＝全局原子） -->
      <g v-if="mode === 'naive'">
        <line v-for="i in T" :key="'na' + i"
          :x1="tX(i - 1)" :y1="tY + 12" :x2="binX(binOf[i - 1])" :y2="gY - 12"
          class="edge global" />
      </g>

      <!-- 层级：线程 → shared（绿＝shared 原子），shared → 全局（红＝全局原子，B 条） -->
      <g v-else>
        <line v-for="i in T" :key="'h1' + i"
          :x1="tX(i - 1)" :y1="tY + 12" :x2="binX(binOf[i - 1])" :y2="shY - 11"
          class="edge shared" />
        <g v-for="b in B" :key="'sh' + (b - 1)">
          <rect :x="binX(b - 1) - 39" :y="shY - 11" width="78" height="24" rx="5"
            class="hist shared-box" :stroke="binColor(b - 1)" />
          <text :x="binX(b - 1)" :y="shY + 5" class="hcnt">{{ binCount[b - 1] }}</text>
          <line :x1="binX(b - 1)" :y1="shY + 13" :x2="binX(b - 1)" :y2="gY - 12" class="edge global" />
        </g>
        <text :x="binStart - 6" :y="shY + 5" class="side">shared</text>
      </g>

      <!-- 全局直方图 -->
      <g v-for="b in B" :key="'g' + (b - 1)">
        <rect :x="binX(b - 1) - 39" :y="gY - 12" width="78" height="26" rx="5"
          class="hist global-box" :stroke="binColor(b - 1)"
          :class="{ hot: mode === 'naive' && binCount[b - 1] > 1 }" />
        <text :x="binX(b - 1)" :y="gY + 5" class="hcnt">{{ binCount[b - 1] }}</text>
        <text :x="binX(b - 1)" :y="gY + 28" class="binlbl">bin {{ b - 1 }}</text>
      </g>
      <text :x="binStart - 6" :y="gY + 5" class="side">global</text>
    </svg>

    <p class="cap">
      直方图统计的痛点是<b>全局原子争抢</b>。朴素做法每个线程直接抢全局直方图（红线 = 全局原子），
      同一 bin 上高度并发。<b>层级聚合</b>先在 block 的 shared memory 里聚出局部直方图（便宜的 shared 原子），
      最后只用<b>常数 {{ B }} 次</b>全局原子合并——全局争抢从「每元素一次」降到「每 block 几次」。
      跨 G 个 block 时，全局原子从 16G 降到 4G。
    </p>
  </div>
</template>

<style scoped>
.demo {
  max-width: 640px; margin: 0 auto;
  font-family: -apple-system, 'PingFang SC', sans-serif; color: #1f2329;
}
.bar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; gap: 8px; }
.seg-btns button {
  font: inherit; cursor: pointer; background: #fff; color: #1f2329;
  border: 1px solid #d9dee5; padding: 6px 14px; font-size: 0.85rem;
}
.seg-btns button:first-child { border-radius: 6px 0 0 6px; }
.seg-btns button:last-child { border-radius: 0 6px 6px 0; border-left: none; }
.seg-btns button.on { background: rgba(22, 119, 255, 0.1); border-color: #1677ff; color: #1677ff; }
.ghost {
  font: inherit; cursor: pointer; background: #fff; color: #5c6370;
  border: 1px solid #d9dee5; border-radius: 6px; padding: 6px 12px; font-size: 0.85rem;
}
.ghost:hover { border-color: #1677ff; color: #1677ff; }
.metrics { font-size: 0.85rem; color: #8a909c; margin-bottom: 8px; }
.metrics b { color: #1677ff; font-size: 1.05rem; }
.metrics b.bad { color: #d9785f; }
.metrics .sep { margin: 0 8px; }
.metrics .ok { color: #2f8f6b; }
.metrics .hint { color: #8a909c; }
.stage { width: 100%; background: #f7f9fc; border: 1px solid #e7ecf3; border-radius: 10px; }
.lbl { font-size: 11px; fill: #8a909c; }
.side { font-size: 10px; fill: #8a909c; text-anchor: end; }
.edge { stroke-width: 1.4; opacity: 0.7; }
.edge.global { stroke: #d9785f; }
.edge.shared { stroke: #74c2a4; opacity: 0.55; }
.hist { fill: #fff; stroke-width: 1.6; }
.global-box.hot { fill: rgba(217, 120, 95, 0.09); }
.hcnt { text-anchor: middle; dominant-baseline: central; font-size: 12px; font-weight: 700; fill: #1f2329; }
.binlbl { text-anchor: middle; font-size: 10px; fill: #8a909c; }
.cap { font-size: 0.85rem; line-height: 1.7; color: #5c6370; margin-top: 10px; }
.cap b { color: #1677ff; }
</style>
