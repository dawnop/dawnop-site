<!-- viz-name: Bitonic 排序网络（数据无关、无分支） -->
<script setup>
import { ref, computed } from 'vue'

// n=8 的双调排序网络：比较器位置完全固定、与数据无关 —— 这正是它适合 SIMT 的原因。
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
        if (l > i) comps.push({ a: i, b: l, asc: (i & k) === 0 })
      }
      stages.push(comps)
    }
  }
  return stages
}
const stages = buildStages(N)

const randData = () => Array.from({ length: N }, () => 10 + Math.floor(Math.random() * 89))
const initial = ref(randData())
const step = ref(0) // 0..stages.length，表示已完成的阶段数

// 依次套用前 step 个阶段，得到当前各线上的值
const values = computed(() => {
  const a = initial.value.slice()
  for (let s = 0; s < step.value; s++) {
    for (const c of stages[s]) {
      const hi = a[c.a] > a[c.b]
      // asc：大的去高索引（屏幕下方）；desc：大的去低索引
      if ((c.asc && hi) || (!c.asc && !hi)) {
        const t = a[c.a]; a[c.a] = a[c.b]; a[c.b] = t
      }
    }
  }
  return a
})

const done = computed(() => step.value >= stages.length)
const rowY = (i) => rowY0 + i * rowH
const colX = (s) => padX + s * gap
const maxV = computed(() => Math.max(...initial.value))
const hue = (v) => Math.round((v / maxV.value) * 215)

const next = () => { if (step.value < stages.length) step.value++ }
const reset = () => { step.value = 0 }
const shuffle = () => { initial.value = randData(); step.value = 0 }

const width = padX + stages.length * gap + 30
const height = rowY0 + N * rowH + 6
</script>

<template>
  <div class="demo">
    <div class="bar">
      <div class="btns">
        <button @click="next" :disabled="done">步进 ▶</button>
        <button @click="reset">重置</button>
        <button @click="shuffle">随机数据</button>
      </div>
      <div class="status">
        阶段 <b>{{ step }}/{{ stages.length }}</b>
        <span class="sep">·</span>
        <span v-if="!done">下一列比较器与数据无关</span>
        <span v-else class="ok">已排序，末 {{ K }} 条线即 top-{{ K }}</span>
      </div>
    </div>

    <svg :viewBox="`0 0 ${width} ${height}`" class="stage">
      <!-- top-k 区域底色（末 K 条线） -->
      <rect
        :x="0" :y="rowY(N - K) - rowH / 2" :width="width" :height="K * rowH"
        :class="{ tkband: true, lit: done }"
      />

      <!-- 导线 -->
      <line
        v-for="i in N" :key="'w' + i"
        :x1="colX(0)" :x2="colX(stages.length)" :y1="rowY(i - 1)" :y2="rowY(i - 1)"
        class="wire"
      />

      <!-- 所有比较器列（静态画出，当前列高亮）。direction 用箭头表示大者去向 -->
      <g v-for="(comp, s) in stages" :key="'s' + s">
        <g v-for="(c, ci) in comp" :key="'c' + s + '-' + ci">
          <line
            :x1="colX(s + 0.5)" :x2="colX(s + 0.5)"
            :y1="rowY(c.a)" :y2="rowY(c.b)"
            class="cmp" :class="{ active: s === step }"
          />
          <!-- 箭头：asc 大者朝下(高索引)，desc 大者朝上 -->
          <polygon
            :points="c.asc
              ? `${colX(s + 0.5) - 4},${rowY(c.b) - 8} ${colX(s + 0.5) + 4},${rowY(c.b) - 8} ${colX(s + 0.5)},${rowY(c.b) - 1}`
              : `${colX(s + 0.5) - 4},${rowY(c.a) + 8} ${colX(s + 0.5) + 4},${rowY(c.a) + 8} ${colX(s + 0.5)},${rowY(c.a) + 1}`"
            class="arrow" :class="{ active: s === step }"
          />
        </g>
      </g>

      <!-- 当前各线上的值，随步进平滑移动到下一列 -->
      <g
        v-for="i in N" :key="'v' + i"
        class="cell"
        :style="{ transform: `translate(${colX(step)}px, ${rowY(i - 1)}px)` }"
      >
        <rect x="-15" y="-12" width="30" height="24" rx="5"
          :fill="`hsl(${hue(values[i - 1])},50%,92%)`"
          :stroke="`hsl(${hue(values[i - 1])},38%,64%)`" />
        <text class="num" :fill="`hsl(${hue(values[i - 1])},34%,42%)`">{{ values[i - 1] }}</text>
      </g>
    </svg>

    <p class="cap">
      8 条导线、固定的比较器网络：每一<b>列</b>是一组同时进行的「比较-交换」，箭头指向较大值的去向。
      关键在于 <b>这些比较器的位置和数据完全无关</b>——无论输入是什么都走同样的步骤，没有数据相关分支，
      天生契合 GPU 的 SIMT。代价是比较器数量 O(k·log²k)，所以只适合小 k。
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
.btns button:hover { border-color: #1677ff; color: #1677ff; }
.btns button:disabled { opacity: 0.45; cursor: not-allowed; color: #1f2329; border-color: #d9dee5; }
.status { font-size: 0.85rem; color: #8a909c; }
.status b { color: #1677ff; }
.status .ok { color: #2f8f6b; }
.status .sep { margin: 0 8px; }
.stage {
  width: 100%;
  background: #f7f9fc;
  border: 1px solid #e7ecf3;
  border-radius: 10px;
}
.tkband { fill: transparent; transition: fill 0.4s; }
.tkband.lit { fill: rgba(124, 193, 163, 0.15); }
.wire { stroke: #cbd2dc; stroke-width: 1.5; }
.cmp { stroke: #dde3ec; stroke-width: 2; transition: stroke 0.3s; }
.cmp.active { stroke: #1677ff; stroke-width: 3; }
.arrow { fill: #dde3ec; transition: fill 0.3s; }
.arrow.active { fill: #1677ff; }
.cell { transition: transform 0.45s cubic-bezier(0.4, 0, 0.2, 1); }
.num { text-anchor: middle; dominant-baseline: central; font-size: 12px; font-weight: 600; }
.cap {
  font-size: 0.85rem;
  line-height: 1.7;
  color: #5c6370;
  margin-top: 10px;
}
.cap b { color: #1677ff; }
</style>
