<script setup>
import { ref, computed, onBeforeUnmount } from 'vue'

// 三条泳道：前端 / 后端 / 七牛
const lanes = [
  { key: 'fe', label: '前端', x: 110 },
  { key: 'be', label: '后端', x: 390 },
  { key: 'qn', label: '七牛', x: 670 },
]
const laneX = (k) => lanes.find((l) => l.key === k).x

// 每一步：从哪条泳道到哪条、y 位置、方向、文案、旁注
const steps = [
  { from: 'fe', to: 'be', y: 78, text: '① 请求上传凭证', note: '「我要传一个文件」' },
  { from: 'be', to: 'fe', y: 120, text: '② 下发一次性凭证', note: '限定到具体 key、短时效；SecretKey 不出后端' },
  { from: 'fe', to: 'qn', y: 168, text: '③ 直传字节', note: '≤4MB 表单直传，更大自动分片 + 断点续传' },
  { from: 'fe', to: 'be', y: 216, text: '④ register 登记', note: '把 path ↔ key 的对应关系写进数据库' },
  { from: 'be', to: 'qn', y: 258, text: '⑤ stat 校验', note: '确认对象真落地，以七牛的大小/类型为准' },
  { from: 'be', to: 'fe', y: 300, text: '⑥ 登记成功', note: '此后「有记录」严格等价于「七牛上真有」' },
]

const idx = ref(-1) // -1 = 未开始；0..n-1 = 已点亮到第几步
let timer = null

function play() {
  stop()
  idx.value = -1
  timer = setInterval(() => {
    if (idx.value >= steps.length - 1) {
      stop()
      return
    }
    idx.value++
  }, 1100)
}
function stop() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}
function next() {
  stop()
  idx.value = idx.value >= steps.length - 1 ? -1 : idx.value + 1
}
function reset() {
  stop()
  idx.value = -1
}
onBeforeUnmount(stop)

const cur = computed(() => (idx.value >= 0 ? steps[idx.value] : null))

// 箭头的起止 x（带一点内缩，箭头不顶到泳道线上）
function arrow(s) {
  const x1 = laneX(s.from)
  const x2 = laneX(s.to)
  const dir = x2 > x1 ? 1 : -1
  return { x1: x1 + dir * 8, x2: x2 - dir * 8, y: s.y, dir }
}
</script>

<template>
  <div class="flow">
    <div class="controls">
      <button class="btn primary" @click="play">▶ 播放</button>
      <button class="btn" @click="next">下一步</button>
      <button class="btn" @click="reset">重置</button>
      <span class="counter">{{ idx < 0 ? '未开始' : `第 ${idx + 1} / ${steps.length} 步` }}</span>
    </div>

    <svg viewBox="0 0 780 340" class="canvas">
      <!-- 泳道 -->
      <g v-for="l in lanes" :key="l.key">
        <rect :x="l.x - 44" y="20" width="88" height="26" rx="6" class="lane-head" />
        <text :x="l.x" y="38" class="lane-label">{{ l.label }}</text>
        <line :x1="l.x" y1="50" :x2="l.x" y2="322" class="lane-line" />
      </g>

      <!-- 步骤箭头 -->
      <g v-for="(s, i) in steps" :key="i">
        <line
          v-bind="{ x1: arrow(s).x1, y1: s.y, x2: arrow(s).x2, y2: s.y }"
          class="msg"
          :class="{ on: i === idx, done: i < idx, dim: idx >= 0 && i > idx }"
          :marker-end="i <= idx ? 'url(#ah-on)' : 'url(#ah)'"
        />
        <text
          :x="(arrow(s).x1 + arrow(s).x2) / 2"
          :y="s.y - 8"
          class="msg-label"
          :class="{ on: i === idx, dim: idx >= 0 && i > idx }"
        >
          {{ s.text }}
        </text>
      </g>

      <defs>
        <marker id="ah" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">
          <path d="M0,0 L7,3 L0,6 Z" fill="#c4cdd5" />
        </marker>
        <marker id="ah-on" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">
          <path d="M0,0 L7,3 L0,6 Z" fill="#1677ff" />
        </marker>
      </defs>
    </svg>

    <div class="note-box">
      <template v-if="cur">
        <span class="note-title">{{ cur.text }}</span>
        <span class="note-body">{{ cur.note }}</span>
      </template>
      <span v-else class="note-idle">点「播放」看一个文件从浏览器到七牛的完整六步，字节全程不经后端。</span>
    </div>
  </div>
</template>

<style scoped>
.flow {
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #1f2328;
}
.controls {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.btn {
  border: 1px solid #d0d7de;
  background: #fff;
  color: #57606a;
  border-radius: 7px;
  padding: 5px 12px;
  font-size: 0.85rem;
  cursor: pointer;
}
.btn:hover {
  border-color: #1677ff;
  color: #1677ff;
}
.btn.primary {
  background: #1677ff;
  border-color: #1677ff;
  color: #fff;
}
.counter {
  margin-left: auto;
  font-size: 0.82rem;
  color: #9aa0a6;
}
.canvas {
  width: 100%;
  height: auto;
  display: block;
}
.lane-head {
  fill: #f6f8fa;
  stroke: #d0d7de;
  stroke-width: 1;
}
.lane-label {
  text-anchor: middle;
  font-size: 13px;
  font-weight: 600;
  fill: #3c4149;
}
.lane-line {
  stroke: #e6e9ec;
  stroke-width: 1.5;
  stroke-dasharray: 3 4;
}
.msg {
  stroke: #c4cdd5;
  stroke-width: 2;
  transition: stroke 0.25s;
}
.msg.done {
  stroke: #9ac2ff;
}
.msg.on {
  stroke: #1677ff;
  stroke-width: 3;
}
.msg.dim {
  stroke: #eceff1;
}
.msg-label {
  text-anchor: middle;
  font-size: 12px;
  fill: #8a9099;
  transition: fill 0.25s;
}
.msg-label.on {
  fill: #1677ff;
  font-weight: 600;
}
.msg-label.dim {
  fill: #d0d3d7;
}
.note-box {
  margin-top: 12px;
  padding: 12px 14px;
  background: #f6f8fa;
  border-radius: 9px;
  min-height: 3.2em;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.note-title {
  font-size: 0.9rem;
  font-weight: 600;
  color: #1677ff;
}
.note-body {
  font-size: 0.88rem;
  color: #3c4149;
  line-height: 1.6;
}
.note-idle {
  font-size: 0.88rem;
  color: #8a9099;
  line-height: 1.6;
}
</style>
