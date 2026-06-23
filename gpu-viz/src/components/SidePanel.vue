<script setup>
defineProps({
  topics: { type: Array, required: true },
  topic: { type: Object, required: true },
  mode: { type: String, default: 'blocks' },
})
const emit = defineEmits(['select', 'set-mode'])
</script>

<template>
  <aside class="panel">
    <div class="brand">dawnop · <span class="accent">GPU 可视化</span></div>

    <nav class="topics">
      <button
        v-for="t in topics"
        :key="t.id"
        :class="{ on: t.id === topic.id }"
        @click="emit('select', t.id)"
      >
        <span>{{ t.icon }}</span>{{ t.title }}
      </button>
    </nav>

    <div class="content">
      <h2>{{ topic.title }}</h2>
      <div class="desc" v-html="topic.desc"></div>

      <div v-if="topic.modes" class="modes">
        <button
          v-for="m in topic.modes"
          :key="m.id"
          :class="{ on: m.id === mode }"
          @click="emit('set-mode', m.id)"
        >
          {{ m.label }}
        </button>
      </div>

      <pre v-if="topic.code" class="code">{{ topic.code }}</pre>

      <p v-if="topic.legend" class="legend">{{ topic.legend }}</p>
    </div>
  </aside>
</template>
