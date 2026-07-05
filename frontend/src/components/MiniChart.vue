<script setup>
// 极简 SVG 面积趋势图（无第三方图表库，契合站点轻量风格）。
// points: [{ label: string, value: number }]；hover 显示最近点的 label + 格式化值。
import { ref, computed } from 'vue'

const props = defineProps({
  points: { type: Array, default: () => [] },
  height: { type: Number, default: 56 },
  color: { type: String, default: '#1677ff' },
  format: { type: Function, default: (v) => String(v) },
})

const W = 300 // viewBox 宽（配合 preserveAspectRatio=none 横向拉伸铺满）
const P = 3 // 上下留白，避免描边被裁

const vals = computed(() => props.points.map((p) => Number(p.value) || 0))
const max = computed(() => Math.max(1, ...vals.value))
const min = computed(() => Math.min(0, ...vals.value))

// 每个点的坐标
const coords = computed(() => {
  const n = props.points.length
  if (!n) return []
  const span = max.value - min.value || 1
  const h = props.height
  return props.points.map((p, i) => {
    const x = n === 1 ? W / 2 : (i / (n - 1)) * W
    const y = P + (1 - ((Number(p.value) || 0) - min.value) / span) * (h - 2 * P)
    return [x, y]
  })
})

const linePath = computed(() =>
  coords.value.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ')
)
const areaPath = computed(() => {
  if (!coords.value.length) return ''
  const first = coords.value[0]
  const last = coords.value[coords.value.length - 1]
  return `M${first[0].toFixed(1)} ${props.height} ` +
    coords.value.map(([x, y]) => `L${x.toFixed(1)} ${y.toFixed(1)}`).join(' ') +
    ` L${last[0].toFixed(1)} ${props.height} Z`
})

const hover = ref(-1)
const svgRef = ref(null)
function onMove(e) {
  const n = props.points.length
  if (!n) return
  const rect = svgRef.value.getBoundingClientRect()
  const rel = (e.clientX - rect.left) / rect.width
  hover.value = Math.max(0, Math.min(n - 1, Math.round(rel * (n - 1))))
}
const hoverPoint = computed(() => (hover.value >= 0 ? props.points[hover.value] : null))
const hoverX = computed(() => (hover.value >= 0 ? coords.value[hover.value][0] : 0))
</script>

<template>
  <div class="mini" :style="{ height: height + 'px' }">
    <svg
      v-if="points.length"
      ref="svgRef"
      class="mini-svg"
      :viewBox="`0 0 ${W} ${height}`"
      preserveAspectRatio="none"
      @mousemove="onMove"
      @mouseleave="hover = -1"
    >
      <defs>
        <linearGradient :id="`g-${color.slice(1)}`" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" :stop-color="color" stop-opacity="0.22" />
          <stop offset="100%" :stop-color="color" stop-opacity="0" />
        </linearGradient>
      </defs>
      <path :d="areaPath" :fill="`url(#g-${color.slice(1)})`" />
      <path :d="linePath" :stroke="color" stroke-width="1.5" fill="none"
        vector-effect="non-scaling-stroke" />
      <line v-if="hover >= 0" :x1="hoverX" :x2="hoverX" y1="0" :y2="height"
        stroke="#c0c4cc" stroke-width="1" vector-effect="non-scaling-stroke" />
    </svg>
    <div v-else class="mini-empty">暂无数据</div>
    <div v-if="hoverPoint" class="mini-tip">
      <b>{{ format(hoverPoint.value) }}</b><span>{{ hoverPoint.label }}</span>
    </div>
  </div>
</template>

<style scoped>
.mini {
  position: relative;
  width: 100%;
}
.mini-svg {
  width: 100%;
  height: 100%;
  display: block;
}
.mini-empty {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--el-text-color-placeholder);
  font-size: 0.8rem;
}
.mini-tip {
  position: absolute;
  top: -2px;
  right: 0;
  display: flex;
  gap: 6px;
  align-items: baseline;
  font-size: 0.75rem;
  color: var(--muted);
  background: rgba(255, 255, 255, 0.9);
  padding: 1px 4px;
  border-radius: 4px;
  pointer-events: none;
}
.mini-tip b {
  color: var(--el-text-color-primary);
}
</style>
