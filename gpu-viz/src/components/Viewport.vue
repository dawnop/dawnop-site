<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { createGpuScene } from '../three/gpuScene'

const props = defineProps({
  topic: { type: Object, required: true },
  mode: { type: String, default: 'blocks' },
})

const container = ref(null)
const canvas = ref(null)
let scene = null

onMounted(() => {
  scene = createGpuScene(canvas.value, container.value)
  scene.setMode(props.mode)
})

onBeforeUnmount(() => scene?.dispose())

watch(() => props.mode, (m) => scene?.setMode(m))
</script>

<template>
  <div ref="container" class="viewport">
    <canvas ref="canvas"></canvas>
    <div class="overlay">
      <h1>{{ topic.title }}</h1>
      <p class="hint">{{ topic.legend }}</p>
    </div>
    <div class="stage-badge">Grid 4×4 · Block 8×8 · Warp 32</div>
    <div v-if="!topic.hasScene" class="todo">该主题的 3D 场景开发中（当前显示线程模型场景）</div>
  </div>
</template>
