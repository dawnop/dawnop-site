<script setup>
import { ref, computed } from 'vue'
import { topics } from './content/topics'
import Viewport from './components/Viewport.vue'
import SidePanel from './components/SidePanel.vue'

const topicId = ref(topics[0].id)
const mode = ref(topics[0].modes ? topics[0].modes[0].id : 'blocks')

const topic = computed(() => topics.find((t) => t.id === topicId.value))

function selectTopic(id) {
  topicId.value = id
  const t = topics.find((x) => x.id === id)
  // 有 3D 场景的主题用其默认模式，其余固定用 blocks 静态展示线程模型
  mode.value = t.modes ? t.modes[0].id : 'blocks'
}
</script>

<template>
  <div class="app">
    <Viewport :topic="topic" :mode="mode" />
    <SidePanel
      :topics="topics"
      :topic="topic"
      :mode="mode"
      @select="selectTopic"
      @set-mode="mode = $event"
    />
  </div>
</template>
