<script setup>
import { ref, watch, computed } from 'vue'

const props = defineProps({
  // 文件元信息：{ filename, content_type }
  file: { type: Object, required: true },
  // 七牛私有签名 URL
  url: { type: String, default: '' },
})

const isImage = computed(() => (props.file.content_type || '').startsWith('image/'))
const isText = computed(() => {
  const t = props.file.content_type || ''
  return t.startsWith('text/') || t.includes('json') || t.includes('xml')
})

const textContent = ref('')
const textError = ref(false)

// 文本预览：直接拉取签名 URL 的内容。若七牛未放行跨域(CORS)，则降级为「新标签打开」。
watch(
  () => props.url,
  async (url) => {
    textContent.value = ''
    textError.value = false
    if (!url || !isText.value) return
    try {
      const resp = await fetch(url)
      textContent.value = await resp.text()
    } catch (e) {
      textError.value = true
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="preview">
    <div v-if="!url" class="hint">加载中…</div>

    <img v-else-if="isImage" :src="url" :alt="file.filename" class="preview-img" />

    <template v-else-if="isText">
      <pre v-if="!textError" class="preview-text">{{ textContent }}</pre>
      <p v-else class="hint">
        无法内联预览（可能是跨域限制）。<a :href="url" target="_blank" rel="noopener">在新标签打开</a>
      </p>
    </template>

    <p v-else class="hint">
      该类型不支持预览。<a :href="url" target="_blank" rel="noopener">下载 / 打开</a>
    </p>
  </div>
</template>

<style scoped>
.preview {
  margin-top: 10px;
}
.preview-img {
  max-width: 100%;
  max-height: 70vh;
  border: 1px solid #ebedf0;
  border-radius: 6px;
}
.preview-text {
  max-height: 60vh;
  overflow: auto;
  background: #f6f8fa;
  padding: 12px;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.85rem;
}
.hint {
  color: #8b949e;
  font-size: 0.9rem;
}
</style>
