<!-- viz-name: mirai · GEMM+epilogue 融合 -->
<script setup>
import { ref, computed, onBeforeUnmount } from 'vue'

// PFFN 里 bmm 后面那截 epilogue：+bias → SiLU → ×vals。
// 未融合（XLA）：每个 op 一个 kernel，中间结果全落显存来回搬。
// 融合（Triton）：一个 kernel，中间结果留在片上，只在头尾碰一次显存。
const ops = [
  { label: 'MatMul', sub: 'bmm' },
  { label: '+ bias', sub: '加偏置' },
  { label: 'SiLU', sub: '激活' },
  { label: '× vals', sub: '逐元素乘' },
]
const boxX = [40, 220, 400, 580]
const cx = (i) => boxX[i] + 60
const gaps = [190, 370, 550]
const bufs = ['R0', 'R1', 'R2']

const mode = ref('unfused')
const fused = computed(() => mode.value === 'fused')

const active = ref(-1)
let timer = null
function walk() {
  stop()
  active.value = -1
  timer = setInterval(() => {
    if (active.value >= ops.length - 1) return stop()
    active.value++
  }, 800)
}
function stop() {
  if (timer) clearInterval(timer)
  timer = null
}
onBeforeUnmount(stop)
function setMode(m) {
  mode.value = m
  active.value = -1
  stop()
}
</script>

<template>
  <div class="fz">
    <div class="controls">
      <div class="toggle">
        <button class="seg" :class="{ sel: !fused }" @click="setMode('unfused')">未融合 · XLA</button>
        <button class="seg" :class="{ sel: fused }" @click="setMode('fused')">融合 · Triton</button>
      </div>
      <button class="btn" @click="walk">▶ 走一遍</button>
    </div>

    <svg viewBox="0 0 740 250" class="canvas">
      <!-- HBM 显存条 -->
      <rect x="20" y="16" width="700" height="40" rx="8" class="hbm" />
      <text x="34" y="41" class="hbm-label">HBM · 显存（慢）</text>

      <!-- 载入 A,B -->
      <line :x1="cx(0)" y1="56" :x2="cx(0)" y2="120" class="io" marker-end="url(#fz-a)" />
      <text :x="cx(0)" y="78" class="io-txt">载入 A,B</text>
      <!-- 写回结果 -->
      <line :x1="cx(3)" y1="120" :x2="cx(3)" y2="56" class="io" marker-end="url(#fz-a)" />
      <text :x="cx(3)" y="78" class="io-txt">写回结果</text>

      <!-- 融合外壳 -->
      <rect
        v-if="fused"
        x="24"
        y="112"
        width="692"
        height="76"
        rx="12"
        class="fuse-shell"
      />
      <text v-if="fused" x="34" y="108" class="fuse-tag">融合 · 1 个 kernel</text>

      <!-- 中间连接：未融合走显存往返，融合走片上 -->
      <g v-for="(g, i) in gaps" :key="i">
        <template v-if="!fused">
          <line :x1="g - 12" y1="120" :x2="g - 12" y2="56" class="rt write" marker-end="url(#fz-a)" />
          <line :x1="g + 12" y1="56" :x2="g + 12" y2="120" class="rt read" marker-end="url(#fz-a)" />
          <text :x="g" y="102" class="buf">{{ bufs[i] }}</text>
        </template>
        <template v-else>
          <line :x1="boxX[i] + 120" y1="150" :x2="boxX[i + 1]" y2="150" class="onchip" marker-end="url(#fz-g)" />
          <circle :cx="g" cy="150" r="4" class="onchip-dot" />
        </template>
      </g>

      <!-- op 盒子 -->
      <g v-for="(op, i) in ops" :key="i">
        <rect
          v-if="!fused"
          :x="boxX[i] - 5"
          y="117"
          width="130"
          height="66"
          rx="10"
          class="kshell"
        />
        <rect
          :x="boxX[i]"
          y="122"
          width="120"
          height="56"
          rx="8"
          class="op"
          :class="{ active: i === active }"
        />
        <text :x="cx(i)" y="147" class="op-title" :class="{ active: i === active }">{{ op.label }}</text>
        <text :x="cx(i)" y="165" class="op-sub">{{ op.sub }}</text>
      </g>

      <defs>
        <marker id="fz-a" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="#9aa4ad" />
        </marker>
        <marker id="fz-g" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="#52a127" />
        </marker>
      </defs>
    </svg>

    <div class="stats">
      <div class="stat" :class="{ hot: !fused }">
        <span class="s-k">kernel 数</span>
        <span class="s-v">{{ fused ? 1 : 4 }}</span>
      </div>
      <div class="stat" :class="{ hot: !fused }">
        <span class="s-k">中间结果落显存</span>
        <span class="s-v">{{ fused ? '0 份' : '3 份' }}</span>
      </div>
    </div>

    <div class="note-box">
      <span class="note-body" v-if="!fused">
        <b>未融合</b>：XLA 把每个 op 单独编成一个 kernel。MatMul 的结果得先写回显存，下一个 kernel 再读回来，`+bias`、`SiLU`、`×vals` 每一步都这么来回搬。三份中间结果 <code>R0/R1/R2</code> 各写一次、再读一次，六次多余的读写全砸在显存带宽上。
      </span>
      <span class="note-body" v-else>
        <b>融合</b>：Inductor 落到 Triton，把四步塞进一个 kernel。MatMul 的结果还在寄存器/shared 里，顺手就把 bias、激活、乘法都算完，最后一次性写回。三份中间结果不碰显存，省下的就是那六次读写。
      </span>
    </div>
  </div>
</template>

<style scoped>
.fz {
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #1f2328;
}
.controls {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.toggle {
  display: inline-flex;
  border: 1px solid #d0d7de;
  border-radius: 8px;
  overflow: hidden;
}
.seg {
  border: none;
  background: #fff;
  color: #57606a;
  padding: 6px 14px;
  font-size: 0.84rem;
  cursor: pointer;
  transition: all 0.15s;
}
.seg + .seg {
  border-left: 1px solid #d0d7de;
}
.seg:hover {
  color: #1677ff;
}
.seg.sel {
  background: #1677ff;
  color: #fff;
}
.btn {
  border: 1px solid #d0d7de;
  background: #fff;
  color: #57606a;
  border-radius: 7px;
  padding: 6px 12px;
  font-size: 0.84rem;
  cursor: pointer;
}
.btn:hover {
  border-color: #1677ff;
  color: #1677ff;
}
.canvas {
  width: 100%;
  height: auto;
  display: block;
}
.hbm {
  fill: #f6f8fa;
  stroke: #d0d7de;
  stroke-width: 1.2;
}
.hbm-label {
  font-size: 12px;
  font-weight: 600;
  fill: #8a9099;
}
.io {
  stroke: #9aa4ad;
  stroke-width: 1.6;
}
.io-txt {
  text-anchor: middle;
  font-size: 10.5px;
  fill: #9aa4ad;
}
.rt {
  stroke-width: 1.8;
}
.rt.write {
  stroke: #e0894b;
}
.rt.read {
  stroke: #e0894b;
}
.buf {
  text-anchor: middle;
  font-size: 11px;
  font-weight: 600;
  fill: #cf7736;
  font-family: 'SFMono-Regular', Menlo, monospace;
}
.onchip {
  stroke: #52a127;
  stroke-width: 2;
}
.onchip-dot {
  fill: #52a127;
}
.fuse-shell {
  fill: #f0f9eb;
  stroke: #b7d99a;
  stroke-width: 1.4;
  stroke-dasharray: 6 4;
}
.fuse-tag {
  text-anchor: start;
  font-size: 11px;
  font-weight: 600;
  fill: #52a127;
}
.kshell {
  fill: none;
  stroke: #e6c6a8;
  stroke-width: 1.2;
  stroke-dasharray: 5 4;
}
.op {
  fill: #fff;
  stroke: #d0d7de;
  stroke-width: 1.4;
  transition: stroke 0.2s, fill 0.2s;
}
.op.active {
  stroke: #1677ff;
  stroke-width: 2.2;
  fill: #eef4ff;
}
.op-title {
  text-anchor: middle;
  font-size: 13px;
  font-weight: 600;
  fill: #3c4149;
  font-family: 'SFMono-Regular', Menlo, monospace;
}
.op-title.active {
  fill: #1677ff;
}
.op-sub {
  text-anchor: middle;
  font-size: 10.5px;
  fill: #9aa4ad;
}
.stats {
  display: flex;
  gap: 10px;
  margin: 14px 0;
}
.stat {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 13px;
  border: 1px solid #e6e9ec;
  border-radius: 9px;
  transition: all 0.2s;
}
.stat.hot {
  border-color: #f0d4bd;
  background: #fdf6f0;
}
.s-k {
  font-size: 0.78rem;
  color: #8a9099;
}
.s-v {
  font-size: 1.25rem;
  font-weight: 700;
  color: #3c4149;
}
.stat.hot .s-v {
  color: #cf7736;
}
.note-box {
  padding: 12px 14px;
  background: #f6f8fa;
  border-radius: 9px;
  min-height: 3.4em;
}
.note-body {
  font-size: 0.88rem;
  color: #3c4149;
  line-height: 1.7;
}
.note-body b {
  color: #1677ff;
}
.note-body code {
  font-family: 'SFMono-Regular', Menlo, monospace;
  background: #f0f2f5;
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 0.85em;
}
@media (max-width: 560px) {
  .stats {
    flex-direction: column;
  }
}
</style>
