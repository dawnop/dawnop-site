<!-- viz-name: Dawn · 孤儿规则棋盘（跨模块重复不可能） -->
<script setup>
import { ref, computed } from 'vue'

// 'ord2' | 'point' | 'app' | 'both'
const placement = ref('point')

const options = [
  { key: 'ord2', label: '放进 ord2（trait 的家）' },
  { key: 'point', label: '放进 point（类型的家）' },
  { key: 'app', label: '放进 app（第三方）' },
  { key: 'both', label: '两处同时放（制造重复）' },
]

const hasImpl = (m) =>
  placement.value === m || (placement.value === 'both' && (m === 'ord2' || m === 'point'))

// 条件导入边：impl 需要对方的名字
const edgeOrd2ToPoint = computed(() => placement.value === 'ord2' || placement.value === 'both')
const edgePointToOrd2 = computed(() => placement.value === 'point' || placement.value === 'both')
const cycle = computed(() => placement.value === 'both')

const verdict = computed(() => {
  switch (placement.value) {
    case 'ord2':
      return {
        ok: true,
        lines: ['✓ 合法：impl 与 trait 同住'],
        note: 'ord2 要写出 impl MyOrd[Point]，得先 use point 拿到 Point 的名字——于是多出一条 ord2 → point 的导入边。',
      }
    case 'point':
      return {
        ok: true,
        lines: ['✓ 合法：impl 与主体类型同住'],
        note: 'point 要写出 impl MyOrd[Point]，得先 use ord2 拿到 MyOrd 的名字——于是多出一条 point → ord2 的导入边。',
      }
    case 'app':
      return {
        ok: false,
        lines: [
          'error: orphan impl: `MyOrd[Point]` may not live here',
          '  = hint: an impl belongs to the module that declares',
          '          `MyOrd` or the one that declares `Point`',
        ],
        note: 'app 既不声明 trait 也不声明类型，孤儿规则直接拦下。若放行，谁 use 了 app 谁的实例选择就变了——一致性从此没法保证。',
      }
    default:
      return {
        ok: false,
        lines: ['error: module dependency cycle: ord2 → point → ord2'],
        note: '两个合法的家各放一份：ord2 需要 use point，point 需要 use ord2——导入成环，模块加载期就是编译错误。这就是那条白捡的定理：模块 DAG + 孤儿规则，跨模块的重复 impl 在结构上不可能存在，一致性检查只需要防同一个模块里写两份。',
      }
  }
})
</script>

<template>
  <div class="orphan">
    <div class="controls">
      <span class="ctrl-label">impl MyOrd[Point] 放哪儿？</span>
      <div class="seg">
        <button
          v-for="o in options"
          :key="o.key"
          :class="{ on: placement === o.key, danger: o.key === 'both' && placement === 'both' }"
          @click="placement = o.key"
        >{{ o.label }}</button>
      </div>
    </div>

    <svg viewBox="0 0 560 250" class="board" role="img" aria-label="三个模块与导入关系">
      <defs>
        <marker id="arr-gray" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#b0b6bd" />
        </marker>
        <marker id="arr-blue" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#1677ff" />
        </marker>
        <marker id="arr-red" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#cf1322" />
        </marker>
      </defs>

      <!-- ord2 模块 -->
      <g>
        <rect x="20" y="24" width="180" height="76" rx="10"
          :class="['box', { placed: hasImpl('ord2'), bad: cycle }]" />
        <text x="36" y="46" class="mod-name">ord2</text>
        <text x="36" y="66" class="mod-decl">pub trait MyOrd[T]</text>
        <text v-if="hasImpl('ord2')" x="36" y="88" class="impl-chip">impl MyOrd[Point] ←</text>
      </g>

      <!-- point 模块 -->
      <g>
        <rect x="360" y="24" width="180" height="76" rx="10"
          :class="['box', { placed: hasImpl('point'), bad: cycle }]" />
        <text x="376" y="46" class="mod-name">point</text>
        <text x="376" y="66" class="mod-decl">pub type Point</text>
        <text v-if="hasImpl('point')" x="376" y="88" class="impl-chip">impl MyOrd[Point] ←</text>
      </g>

      <!-- app 模块 -->
      <g>
        <rect x="190" y="160" width="180" height="76" rx="10"
          :class="['box', { placed: hasImpl('app'), rejected: placement === 'app' }]" />
        <text x="206" y="182" class="mod-name">app</text>
        <text x="206" y="202" class="mod-decl">use ord2  ·  use point</text>
        <text v-if="hasImpl('app')" x="206" y="224" class="impl-chip bad-chip">impl MyOrd[Point] ✗</text>
      </g>

      <!-- 固定导入：app → ord2 / app → point -->
      <path d="M 236 160 L 138 104" fill="none" stroke="#b0b6bd" stroke-width="1.6" marker-end="url(#arr-gray)" />
      <path d="M 324 160 L 422 104" fill="none" stroke="#b0b6bd" stroke-width="1.6" marker-end="url(#arr-gray)" />

      <!-- 条件导入：ord2 → point（impl 在 ord2 时） -->
      <path v-if="edgeOrd2ToPoint" d="M 200 48 L 354 48" fill="none"
        :stroke="cycle ? '#cf1322' : '#1677ff'" stroke-width="2"
        :marker-end="cycle ? 'url(#arr-red)' : 'url(#arr-blue)'" />
      <text v-if="edgeOrd2ToPoint" x="238" y="40" :class="['edge-label', { 'edge-bad': cycle }]">use point</text>

      <!-- 条件导入：point → ord2（impl 在 point 时） -->
      <path v-if="edgePointToOrd2" d="M 360 84 L 206 84" fill="none"
        :stroke="cycle ? '#cf1322' : '#1677ff'" stroke-width="2"
        :marker-end="cycle ? 'url(#arr-red)' : 'url(#arr-blue)'" />
      <text v-if="edgePointToOrd2" x="242" y="102" :class="['edge-label', { 'edge-bad': cycle }]">use ord2</text>

      <text v-if="cycle" x="280" y="136" class="cycle-badge">⟳ 成环</text>
    </svg>

    <div class="verdict mono" :class="verdict.ok ? 'ok' : 'bad'">
      <div v-for="(l, i) in verdict.lines" :key="i">{{ l }}</div>
    </div>
    <div class="foot">{{ verdict.note }}</div>
  </div>
</template>

<style scoped>
.orphan {
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #1f2328;
}
.mono {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, 'Cascadia Mono', monospace;
}
.controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.ctrl-label {
  font-size: 0.82rem;
  color: #8a9099;
}
.seg {
  display: flex;
  flex-wrap: wrap;
  border: 1px solid #d0d7de;
  border-radius: 7px;
  overflow: hidden;
}
.seg button {
  font: inherit;
  font-size: 0.8rem;
  padding: 4px 10px;
  border: none;
  background: #fff;
  color: #3c4149;
  cursor: pointer;
}
.seg button + button {
  border-left: 1px solid #d0d7de;
}
.seg button.on {
  background: #1677ff;
  color: #fff;
  font-weight: 600;
}
.seg button.on.danger {
  background: #cf1322;
}
.board {
  display: block;
  width: 100%;
  max-width: 620px;
  margin: 0 auto;
  background: #fafbfc;
  border: 1px solid #e4e8ec;
  border-radius: 10px;
}
.box {
  fill: #fff;
  stroke: #d0d7de;
  stroke-width: 1.4;
}
.box.placed {
  fill: #eef4ff;
  stroke: #1677ff;
}
.box.placed.bad {
  fill: #fef0f0;
  stroke: #cf1322;
}
.box.rejected {
  fill: #fef0f0;
  stroke: #cf1322;
}
.mod-name {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, 'Cascadia Mono', monospace;
  font-size: 14px;
  font-weight: 700;
  fill: #1f2328;
}
.mod-decl {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, 'Cascadia Mono', monospace;
  font-size: 11.5px;
  fill: #5b6b8c;
}
.impl-chip {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, 'Cascadia Mono', monospace;
  font-size: 11.5px;
  font-weight: 600;
  fill: #1677ff;
}
.impl-chip.bad-chip {
  fill: #cf1322;
}
.edge-label {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, 'Cascadia Mono', monospace;
  font-size: 11px;
  fill: #1677ff;
}
.edge-label.edge-bad {
  fill: #cf1322;
}
.cycle-badge {
  font-size: 12.5px;
  font-weight: 700;
  fill: #cf1322;
  text-anchor: middle;
}
.verdict {
  margin-top: 12px;
  border-radius: 9px;
  padding: 10px 14px;
  font-size: 0.83rem;
  line-height: 1.7;
  white-space: pre-wrap;
}
.verdict.ok {
  background: #f0f9eb;
  border: 1px solid #cde8b8;
  color: #389e0d;
}
.verdict.bad {
  background: #fef0f0;
  border: 1px solid #f5c6c6;
  color: #cf1322;
}
.foot {
  margin-top: 10px;
  font-size: 0.83rem;
  color: #8a9099;
  line-height: 1.7;
}
</style>
