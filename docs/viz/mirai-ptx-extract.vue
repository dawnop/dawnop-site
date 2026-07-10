<!-- viz-name: mirai · PTX 藏在运行时 -->
<script setup>
import { ref, computed } from 'vue'

// 4 步：读文件 → 打补丁 → 执行 → 落盘。演示「PTX 只在 launch 时才出现」。
const stages = [
  { label: '静态读文件', note: 'output_code.py 里只有 Python 启动代码，描述「该 launch 哪个 kernel」。此刻 PTX 根本还不存在。' },
  { label: 'AST 打补丁', note: '在每个 kernel launch 的位置插一段钩子，准备在 kernel 真的跑起来时把 PTX 落盘。' },
  { label: '子进程执行', note: 'kernel 首次 launch，Triton 才即时（JIT）把它编成 PTX。这一刻，PTX 第一次出现在内存里。' },
  { label: 'PTX 落盘', note: '钩子抓住这一刻，把 PTX 和张量 shape 写到磁盘。至此才真正「抠到」了 PTX。' },
]
const step = ref(0)
function next() {
  step.value = (step.value + 1) % stages.length
}
function reset() {
  step.value = 0
}
const cur = computed(() => stages[step.value])
const patched = computed(() => step.value >= 1)
const running = computed(() => step.value === 2)
const extracted = computed(() => step.value >= 3)
</script>

<template>
  <div class="ex">
    <div class="controls">
      <button class="btn primary" @click="next">下一步 ›</button>
      <button class="btn" @click="reset">重置</button>
      <span class="counter">{{ step + 1 }} / {{ stages.length }} · {{ cur.label }}</span>
    </div>

    <div class="panes">
      <div class="pane">
        <div class="pane-head">output_code.py <span class="muted">Inductor 生成</span></div>
        <pre class="code"><span class="cmt"># 只有 Python，描述该 launch 哪个 kernel</span>
def call(args):
    x, y = args
    <span class="hl" :class="{ active: running }">triton_kernel[grid](x, y, out)</span>
<span v-if="patched" class="hook" :class="{ fire: running || extracted }">    dump_ptx(triton_kernel)  <span class="cmt2"># ← 补丁插入的钩子</span></span>
    return out</pre>
      </div>

      <div class="arrow">→</div>

      <div class="pane">
        <div class="pane-head">PTX 产物</div>
        <div class="ptx" :class="{ empty: !extracted, running }">
          <template v-if="extracted">
            <pre class="ptx-code">.version 8.2
.target sm_80
.visible .entry triton_kernel(
  .param .u64 x, .param .u64 y ...
) { ... }</pre>
            <div class="meta">＋ shape 元数据 已落盘</div>
          </template>
          <template v-else-if="running">
            <span class="spin">⟳</span> Triton 正在 JIT 编译…
          </template>
          <template v-else>
            <span class="q">?</span>
            <span class="q-txt">还不存在</span>
          </template>
        </div>
      </div>
    </div>

    <div class="note-box">
      <span class="note-title">{{ cur.label }}</span>
      <span class="note-body">{{ cur.note }}</span>
    </div>
  </div>
</template>

<style scoped>
.ex {
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
.panes {
  display: flex;
  align-items: stretch;
  gap: 10px;
}
.pane {
  flex: 1;
  min-width: 0;
  border: 1px solid #e6e9ec;
  border-radius: 9px;
  overflow: hidden;
}
.pane-head {
  padding: 7px 11px;
  background: #f6f8fa;
  border-bottom: 1px solid #e6e9ec;
  font-size: 0.78rem;
  font-weight: 600;
  color: #57606a;
}
.muted {
  color: #b0b6bd;
  font-weight: 400;
}
.code {
  margin: 0;
  padding: 11px;
  font-family: 'SFMono-Regular', Menlo, Consolas, monospace;
  font-size: 0.76rem;
  line-height: 1.7;
  color: #3c4149;
  white-space: pre-wrap;
}
.cmt,
.cmt2 {
  color: #a0a6ad;
}
.hl {
  border-radius: 4px;
  padding: 0 2px;
  transition: background 0.25s;
}
.hl.active {
  background: #fff2c9;
  box-shadow: 0 0 0 1px #ffe08a;
}
.hook {
  display: block;
  color: #1677ff;
  background: #eef4ff;
  border-radius: 4px;
  transition: opacity 0.25s;
}
.hook.fire {
  background: #dbeafe;
}
.arrow {
  align-self: center;
  color: #c4cdd5;
  font-size: 1.3rem;
}
.ptx {
  min-height: 120px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 6px;
  padding: 11px;
}
.ptx.empty {
  align-items: center;
  color: #c4cdd5;
}
.ptx.running {
  align-items: center;
  justify-content: center;
  color: #1677ff;
  font-size: 0.85rem;
}
.q {
  font-size: 2rem;
  font-weight: 700;
  color: #d0d7de;
}
.q-txt {
  font-size: 0.8rem;
  color: #b0b6bd;
}
.spin {
  display: inline-block;
  animation: sp 0.9s linear infinite;
  font-size: 1.1rem;
}
@keyframes sp {
  to {
    transform: rotate(360deg);
  }
}
.ptx-code {
  margin: 0;
  font-family: 'SFMono-Regular', Menlo, Consolas, monospace;
  font-size: 0.72rem;
  line-height: 1.6;
  color: #0a7d33;
  white-space: pre-wrap;
}
.meta {
  font-size: 0.76rem;
  color: #389e0d;
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
  line-height: 1.65;
}
@media (max-width: 600px) {
  .panes {
    flex-direction: column;
  }
  .arrow {
    transform: rotate(90deg);
  }
}
</style>
