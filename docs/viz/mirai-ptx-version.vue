<!-- viz-name: mirai · PTX 版本兼容 -->
<script setup>
import { ref, computed } from 'vue'

// CUDA 版本 → 该版本引入的 PTX ISA 版本。这是 NVIDIA 官方的对应表（查表，不是公式）。
// 注意尾部：12.5 和 12.6 都是 8.5——「主版本减 4」的口诀到这里就不准了。
const CUDA_TO_ISA = {
  '11.7': '7.7',
  '11.8': '7.8',
  '12.1': '8.1',
  '12.2': '8.2',
  '12.4': '8.4',
  '12.6': '8.5',
}
const cudas = Object.keys(CUDA_TO_ISA)
const tuple = (isa) => isa.split('.').map(Number)
function cmp(a, b) {
  const [a1, a2] = tuple(a)
  const [b1, b2] = tuple(b)
  return a1 !== b1 ? a1 - b1 : a2 - b2
}

// 左：PyTorch 机器（生成 PTX 用的 CUDA）；右：TF 机器（跑 build.sh 的 ptxas 的 CUDA）
const genCuda = ref('12.4')
const tgtCuda = ref('12.2')

const ptxVer = computed(() => CUDA_TO_ISA[genCuda.value]) // 生成的 PTX 里写下的 .version
const tgtMax = computed(() => CUDA_TO_ISA[tgtCuda.value]) // 目标 ptxas 支持到的最高 .version
const rel = computed(() => cmp(tgtMax.value, ptxVer.value)) // >0 目标更新, =0 相等, <0 目标更旧
const ok = computed(() => rel.value >= 0)
</script>

<template>
  <div class="ver">
    <div class="sides">
      <div class="side">
        <div class="side-head">PyTorch 机器 · 生成 PTX 的 CUDA</div>
        <div class="chips">
          <button
            v-for="v in cudas"
            :key="v"
            class="chip"
            :class="{ sel: genCuda === v }"
            @click="genCuda = v"
          >
            {{ v }}
          </button>
        </div>
        <div class="derive">
          PTX 里写下 <code>.version {{ ptxVer }}</code>
        </div>
      </div>

      <div class="side">
        <div class="side-head">TF 机器 · build.sh 里 ptxas 的 CUDA</div>
        <div class="chips">
          <button
            v-for="v in cudas"
            :key="v"
            class="chip"
            :class="{ sel: tgtCuda === v }"
            @click="tgtCuda = v"
          >
            {{ v }}
          </button>
        </div>
        <div class="derive">
          ptxas 最高认到 <code>.version {{ tgtMax }}</code>
        </div>
      </div>
    </div>

    <div class="verdict" :class="ok ? 'good' : 'bad'">
      <span class="icon">{{ ok ? '✓' : '✕' }}</span>
      <span v-if="rel > 0" class="v-text">
        <b>兼容</b>：目标 ptxas 更新（认到 <code>{{ tgtMax }}</code>），能吃下更老的 <code>.version {{ ptxVer }}</code>。<b>ptxas 向后兼容，新的能编老的。</b>
      </span>
      <span v-else-if="rel === 0" class="v-text">
        <b>正好一致</b>：两边都是 <code>.version {{ ptxVer }}</code>。这是 mirai 最想看到的情况——它把 ptxas 钉到目标版本再生成，本就期望严丝合缝地相等。
      </span>
      <span v-else class="v-text">
        <b>不兼容</b>：PTX 是 <code>.version {{ ptxVer }}</code>，比目标 ptxas 认到的 <code>{{ tgtMax }}</code> 还新，老的编不动新的。build.sh 会当场炸出：
        <code class="err">ptxas fatal : Unsupported .version {{ ptxVer }}; current version is '{{ tgtMax }}'</code>
      </span>
    </div>

    <p class="hint">
      规则是单向的：<b>目标机 CUDA ≥ 生成机 CUDA</b> 就安全，反过来（在更新的机器上生成）就会给老 ptxas 递上它读不懂的 PTX。mirai 更省事，直接在生成时把 ptxas 钉到目标、要求两边完全相等——一旦不等，就说明钉的环境变量没生效，当场报错。
    </p>
  </div>
</template>

<style scoped>
.ver {
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #1f2328;
}
.sides {
  display: flex;
  gap: 12px;
}
.side {
  flex: 1;
  min-width: 0;
  border: 1px solid #e6e9ec;
  border-radius: 9px;
  padding: 12px;
}
.side-head {
  font-size: 0.8rem;
  font-weight: 600;
  color: #57606a;
  margin-bottom: 10px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chip {
  border: 1px solid #d0d7de;
  background: #fff;
  color: #57606a;
  border-radius: 20px;
  padding: 4px 12px;
  font-size: 0.82rem;
  font-family: 'SFMono-Regular', Menlo, Consolas, monospace;
  cursor: pointer;
  transition: all 0.15s;
}
.chip:hover {
  border-color: #1677ff;
  color: #1677ff;
}
.chip.sel {
  background: #1677ff;
  border-color: #1677ff;
  color: #fff;
}
.derive {
  margin-top: 11px;
  font-size: 0.82rem;
  color: #8a9099;
}
.derive code {
  color: #3c4149;
}
code {
  font-family: 'SFMono-Regular', Menlo, Consolas, monospace;
  background: #f0f2f5;
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 0.85em;
}
.verdict {
  margin-top: 14px;
  padding: 13px 15px;
  border-radius: 9px;
  display: flex;
  align-items: flex-start;
  gap: 11px;
  line-height: 1.7;
}
.verdict.good {
  background: #f0f9eb;
  border: 1px solid #cdeaba;
}
.verdict.bad {
  background: #fef2f2;
  border: 1px solid #f8caca;
}
.icon {
  flex: none;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.9rem;
  font-weight: 700;
  color: #fff;
}
.good .icon {
  background: #52a127;
}
.bad .icon {
  background: #e04b4b;
}
.v-text {
  font-size: 0.86rem;
  color: #3c4149;
}
.good .v-text b {
  color: #3a7d1c;
}
.bad .v-text b {
  color: #c0392b;
}
.v-text code {
  background: rgba(255, 255, 255, 0.6);
}
.v-text code.err {
  display: block;
  margin-top: 8px;
  color: #c0392b;
  background: #fff;
  border: 1px solid #f2d0d0;
  padding: 7px 10px;
  line-height: 1.5;
  white-space: pre-wrap;
}
.hint {
  margin: 12px 2px 0;
  font-size: 0.82rem;
  color: #8a9099;
  line-height: 1.7;
}
.hint b {
  color: #57606a;
}
@media (max-width: 560px) {
  .sides {
    flex-direction: column;
  }
}
</style>
