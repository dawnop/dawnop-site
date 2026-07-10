<!-- viz-name: mirai · PTX 握手层 -->
<script setup>
import { ref, computed } from 'vue'

// 三个可点的「层」：PyTorch 生成链 / PTX 交汇层 / TF 装载链
const layers = {
  pt: {
    label: 'PyTorch 侧',
    note: '@mirai.op 标记的函数经 torch.compile 编成 Triton kernel，自动调优后落到 PTX。这一侧只管一件事：把算子编好、调优好。',
  },
  ptx: {
    label: 'PTX 交汇层',
    note: 'PTX 是 NVIDIA 的虚拟指令集，不认识 PyTorch、也不认识 TensorFlow，只是一段自包含的 GPU 程序。两个框架唯一需要达成一致的东西，就在这一层。',
  },
  tf: {
    label: 'TensorFlow 侧',
    note: '一个自包含的 C++ 自定义算子把 PTX 载入 GPU 并 launch。它不关心这段 PTX 原本来自哪个框架，装回去就能跑。',
  },
}

const sel = ref('') // '' | 'pt' | 'ptx' | 'tf'
function pick(k) {
  sel.value = sel.value === k ? '' : k
}
const on = (k) => sel.value === k
const dim = (k) => sel.value !== '' && sel.value !== k
const cur = computed(() => (sel.value ? layers[sel.value] : null))
</script>

<template>
  <div class="bridge">
    <svg viewBox="0 0 720 340" class="canvas">
      <!-- 顶部两张图：图这一层谈不拢 -->
      <g class="clickable" :class="{ on: on('pt'), dim: dim('pt') }" @click="pick('pt')">
        <rect x="70" y="26" width="200" height="48" rx="9" class="box pt-fill" />
        <text x="170" y="47" class="t-title">PyTorch 计算图</text>
        <text x="170" y="64" class="t-sub">@mirai.op 标记的函数</text>
      </g>
      <g class="clickable" :class="{ on: on('tf'), dim: dim('tf') }" @click="pick('tf')">
        <rect x="450" y="26" width="200" height="48" rx="9" class="box tf-fill" />
        <text x="550" y="47" class="t-title">TensorFlow 计算图</text>
        <text x="550" y="64" class="t-sub">线上部署栈</text>
      </g>
      <!-- 图与图之间：谈不拢 -->
      <line x1="278" y1="50" x2="442" y2="50" class="gap-line" />
      <text x="360" y="42" class="gap-label">图这一层 · 谈不拢</text>

      <!-- PyTorch 生成链（往下到 PTX） -->
      <g class="clickable" :class="{ on: on('pt'), dim: dim('pt') }" @click="pick('pt')">
        <line x1="170" y1="74" x2="170" y2="120" class="flow" :marker-end="on('pt') ? 'url(#a-on)' : 'url(#a)'" />
        <rect x="86" y="120" width="168" height="42" rx="8" class="box" />
        <text x="170" y="141" class="t-title">torch.compile → Triton</text>
        <text x="170" y="156" class="t-sub">max_autotune 调优</text>
        <line x1="170" y1="162" x2="230" y2="236" class="flow" :marker-end="on('pt') ? 'url(#a-on)' : 'url(#a)'" />
      </g>

      <!-- TF 装载链（从 PTX 往上） -->
      <g class="clickable" :class="{ on: on('tf'), dim: dim('tf') }" @click="pick('tf')">
        <line x1="550" y1="74" x2="550" y2="120" class="flow" :marker-end="on('tf') ? 'url(#a-on)' : 'url(#a)'" />
        <rect x="466" y="120" width="168" height="42" rx="8" class="box" />
        <text x="550" y="141" class="t-title">C++ TF 自定义算子</text>
        <text x="550" y="156" class="t-sub">载入 PTX + CUDA launch</text>
        <line x1="490" y1="236" x2="550" y2="162" class="flow" :marker-end="on('tf') ? 'url(#a-on)' : 'url(#a)'" />
      </g>

      <!-- 中央 PTX 交汇条 -->
      <g class="clickable" :class="{ on: on('ptx'), dim: dim('ptx') }" @click="pick('ptx')">
        <rect x="230" y="236" width="260" height="54" rx="10" class="box ptx-fill" />
        <text x="360" y="260" class="ptx-title">PTX</text>
        <text x="360" y="279" class="t-sub">框架无关的共同底 · 驱动即时编成 SASS</text>
      </g>

      <defs>
        <marker id="a" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">
          <path d="M0,0 L7,3 L0,6 Z" fill="#c4cdd5" />
        </marker>
        <marker id="a-on" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">
          <path d="M0,0 L7,3 L0,6 Z" fill="#1677ff" />
        </marker>
      </defs>
    </svg>

    <div class="note-box">
      <template v-if="cur">
        <span class="note-title">{{ cur.label }}</span>
        <span class="note-body">{{ cur.note }}</span>
      </template>
      <span v-else class="note-idle">点上面的任一层看它的角色。两条框架的路各走各的，直到 PTX 才并到一起。</span>
    </div>
  </div>
</template>

<style scoped>
.bridge {
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #1f2328;
}
.canvas {
  width: 100%;
  height: auto;
  display: block;
}
.clickable {
  cursor: pointer;
}
.box {
  fill: #fff;
  stroke: #d0d7de;
  stroke-width: 1.4;
  transition: stroke 0.2s, fill 0.2s;
}
.pt-fill {
  fill: #f6f8fa;
}
.tf-fill {
  fill: #f6f8fa;
}
.ptx-fill {
  fill: #eef4ff;
  stroke: #bcd4ff;
}
.clickable.on .box {
  stroke: #1677ff;
  stroke-width: 2;
  fill: #eef4ff;
}
.clickable.dim {
  opacity: 0.4;
}
.t-title {
  text-anchor: middle;
  font-size: 13px;
  font-weight: 600;
  fill: #3c4149;
}
.t-sub {
  text-anchor: middle;
  font-size: 11px;
  fill: #8a9099;
}
.ptx-title {
  text-anchor: middle;
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.12em;
  fill: #1677ff;
}
.flow {
  stroke: #c4cdd5;
  stroke-width: 2;
  transition: stroke 0.2s;
}
.clickable.on .flow {
  stroke: #1677ff;
}
.gap-line {
  stroke: #d0d7de;
  stroke-width: 1.5;
  stroke-dasharray: 4 4;
}
.gap-label {
  text-anchor: middle;
  font-size: 11px;
  fill: #b0b6bd;
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
