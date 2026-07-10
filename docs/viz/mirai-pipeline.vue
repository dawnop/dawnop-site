<!-- viz-name: mirai · 五阶段管线 -->
<script setup>
import { ref, computed, onBeforeUnmount } from 'vue'

// 整条路分在两台机器上：PyTorch 机器负责生成（3 步），TF 机器负责落地（2 步）。
const steps = [
  {
    env: 'PyTorch 机器',
    title: '编译 + 跑前向/反向',
    note: 'torch.compile（max_autotune）把函数编成 Triton kernel，跑一遍前向、再 loss.backward() 一次，逼 Inductor 把前向和反向两套 kernel 都吐出来（output_code.py）。执行前先清 inductor 缓存，保证每次重新生成。',
  },
  {
    env: 'PyTorch 机器',
    title: '抠出 PTX',
    note: 'PTX 是惰性编译的，只在 kernel 首次 launch 时才由 Triton 生成。于是定位 output_code.py、给它打补丁在 launch 点插钩子，再开一个干净子进程跑一遍，把 PTX 和 shape 落盘。落盘时顺手校验 PTX 版本对不对得上目标机的 ptxas（详见后文那个坑）。',
  },
  {
    env: 'PyTorch 机器',
    title: '生成 TF 侧产物',
    note: '用 Jinja2 把每个 kernel 渲染成自包含的 C++ TF 自定义算子（PTX 载入 + CUDA launch + shape 推断），再生成 build.sh 和用 tf.custom_gradient 缝合前/反向的 Python 包装层，全部落到 generated/。到这里 build() 就收工了。',
  },
  {
    env: 'TF 机器',
    title: '编译 · bash build.sh',
    note: '把 generated/ 拷到装了 TensorFlow 的机器，一句 bash build.sh 把 .cc 编成 .so。两套框架分两台机器，各自的 CUDA 互不打架。',
  },
  {
    env: 'TF 机器',
    title: '加载算子 · 训练 / 推理',
    note: 'TF 里 import 那个 Python 包装层，算子就像原生 op 一样用。因为缝了 tf.custom_gradient，它在 TF 图里可导，训练、推理两头都能接。',
  },
]

const idx = ref(-1)
let timer = null
function play() {
  stop()
  idx.value = -1
  timer = setInterval(() => {
    if (idx.value >= steps.length - 1) return stop()
    idx.value++
  }, 1200)
}
function stop() {
  if (timer) clearInterval(timer)
  timer = null
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
</script>

<template>
  <div class="pipe">
    <div class="controls">
      <button class="btn primary" @click="play">▶ 播放</button>
      <button class="btn" @click="next">下一步</button>
      <button class="btn" @click="reset">重置</button>
      <span class="counter">{{ idx < 0 ? '未开始' : `第 ${idx + 1} / ${steps.length} 步` }}</span>
    </div>

    <ol class="rows">
      <li
        v-for="(s, i) in steps"
        :key="i"
        class="row"
        :class="{ on: i === idx, done: i < idx, dim: idx >= 0 && i > idx, tf: s.env === 'TF 机器' }"
        @click="idx = i"
      >
        <span class="num">{{ i + 1 }}</span>
        <span class="body">
          <span class="title">{{ s.title }}</span>
          <span class="env" :class="{ 'env-tf': s.env === 'TF 机器' }">{{ s.env }}</span>
        </span>
      </li>
    </ol>

    <div class="note-box">
      <template v-if="cur">
        <span class="note-title">{{ cur.title }}</span>
        <span class="note-body">{{ cur.note }}</span>
      </template>
      <span v-else class="note-idle">点「播放」看一个 PyTorch 函数怎么跨过两台机器，变成线上能训能推的 TF 算子。前 3 步在 PyTorch 机器生成，后 2 步在 TF 机器落地。</span>
    </div>
  </div>
</template>

<style scoped>
.pipe {
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #1f2328;
}
.controls {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
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
.rows {
  list-style: none;
  margin: 0;
  padding: 0;
}
.row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid #e6e9ec;
  border-radius: 9px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s, opacity 0.2s;
}
.row:hover {
  border-color: #bcd4ff;
}
.row.on {
  border-color: #1677ff;
  background: #eef4ff;
}
.row.done {
  border-color: #9ac2ff;
}
.row.dim {
  opacity: 0.5;
}
.num {
  flex: none;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: #f0f2f5;
  color: #8a9099;
  font-size: 0.82rem;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}
.row.on .num,
.row.done .num {
  background: #1677ff;
  color: #fff;
}
.body {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex: 1;
  min-width: 0;
}
.title {
  font-size: 0.95rem;
  font-weight: 550;
  color: #3c4149;
}
.env {
  flex: none;
  font-size: 0.72rem;
  color: #8a9099;
  background: #f6f8fa;
  border: 1px solid #e6e9ec;
  border-radius: 20px;
  padding: 2px 9px;
}
.env-tf {
  color: #b8730b;
  background: #fff7e8;
  border-color: #ffe2b0;
}
.note-box {
  margin-top: 6px;
  padding: 12px 14px;
  background: #f6f8fa;
  border-radius: 9px;
  min-height: 3.6em;
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
  line-height: 1.65;
}
.note-idle {
  font-size: 0.88rem;
  color: #8a9099;
  line-height: 1.65;
}
</style>
