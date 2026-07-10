<!-- viz-name: mirai · 静态 vs 动态 shape -->
<script setup>
import { ref, computed } from 'vue'

const mode = ref('static') // 'static' | 'dynamic'
const isStatic = computed(() => mode.value === 'static')

const staticCode = `// PffnFwd.cc  —  静态 shape
constexpr int B = 5000;   // shape 烤成
constexpr int T = 32;     // 编译期常量
constexpr int D = 512;
// kernel 里的循环边界、tiling
// 全按这些常量特化`

const dynamicCode = `// PffnFwd.cc  —  dynamic=True
int B = inputs.dim_size(0);  // 运行时
int T = inputs.dim_size(1);  // 从张量读
int D = inputs.dim_size(2);
// 一个二进制吃变长 batch
// 不随 shape 重新生成`
</script>

<template>
  <div class="sm">
    <div class="toggle">
      <button class="seg" :class="{ sel: isStatic }" @click="mode = 'static'">静态 shape（默认）</button>
      <button class="seg" :class="{ sel: !isStatic }" @click="mode = 'dynamic'">动态 shape（dynamic=True）</button>
    </div>

    <pre class="code">{{ isStatic ? staticCode : dynamicCode }}</pre>

    <div class="bars">
      <div class="bar-row">
        <span class="bar-label">优化空间</span>
        <div class="track"><div class="fill" :style="{ width: isStatic ? '100%' : '62%' }"></div></div>
        <span class="bar-tag">{{ isStatic ? '最大' : '让掉一部分' }}</span>
      </div>
      <div class="bar-row">
        <span class="bar-label">灵活性</span>
        <div class="track"><div class="fill" :style="{ width: isStatic ? '35%' : '100%' }"></div></div>
        <span class="bar-tag">{{ isStatic ? '换 shape 要重编' : '一个二进制通吃' }}</span>
      </div>
    </div>

    <div class="note-box">
      <span class="note-body" v-if="isStatic">
        <b>静态</b>：把输入 shape 当编译期常量烤进 kernel，循环边界、tiling 全按具体尺寸特化，换来最大程度的优化——代价是 shape 一变就得重新生成、重新编译。
      </span>
      <span class="note-body" v-else>
        <b>动态</b>：shape 在运行时才从张量读，生成 shape-generic 的算子，一个二进制吃变长的 batch、不用重编——代价是让掉一部分静态特化的优化空间。
      </span>
    </div>
  </div>
</template>

<style scoped>
.sm {
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #1f2328;
}
.toggle {
  display: inline-flex;
  border: 1px solid #d0d7de;
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 12px;
}
.seg {
  border: none;
  background: #fff;
  color: #57606a;
  padding: 7px 16px;
  font-size: 0.85rem;
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
.code {
  margin: 0 0 14px;
  padding: 13px 15px;
  background: #f6f8fa;
  border: 1px solid #e6e9ec;
  border-radius: 9px;
  font-family: 'SFMono-Regular', Menlo, Consolas, monospace;
  font-size: 0.78rem;
  line-height: 1.7;
  color: #3c4149;
  white-space: pre-wrap;
  min-height: 128px;
}
.bars {
  display: flex;
  flex-direction: column;
  gap: 9px;
  margin-bottom: 14px;
}
.bar-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.bar-label {
  flex: none;
  width: 4.5em;
  font-size: 0.8rem;
  color: #57606a;
}
.track {
  flex: 1;
  height: 8px;
  background: #eceff1;
  border-radius: 5px;
  overflow: hidden;
}
.fill {
  height: 100%;
  background: #1677ff;
  border-radius: 5px;
  transition: width 0.35s ease;
}
.bar-tag {
  flex: none;
  width: 8em;
  text-align: right;
  font-size: 0.78rem;
  color: #8a9099;
}
.note-box {
  padding: 12px 14px;
  background: #f6f8fa;
  border-radius: 9px;
}
.note-body {
  font-size: 0.88rem;
  color: #3c4149;
  line-height: 1.65;
}
.note-body b {
  color: #1677ff;
}
@media (max-width: 560px) {
  .toggle {
    display: flex;
  }
  .seg {
    flex: 1;
  }
  .bar-tag {
    width: 6em;
  }
}
</style>
