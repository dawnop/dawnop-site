<script setup>
import { computed } from 'vue'
import { renderMarkdown } from '../utils/safeMarkdown'
const props = defineProps({ source: { type: String, default: '' } })
const html = computed(() => renderMarkdown(props.source))
</script>
<template><div class="markdown-document" v-html="html" /></template>
<style scoped>
.markdown-document {
  font-size: 14px;
  line-height: 1.8;
  overflow-wrap: anywhere;
  color: var(--fg);
}
.markdown-document :deep(> :first-child) {
  margin-top: 0;
}
.markdown-document :deep(h1) {
  font-size: 26px;
}
.markdown-document :deep(h2) {
  font-size: 22px;
}
.markdown-document :deep(h3) {
  font-size: 18px;
}
.markdown-document :deep(a) {
  color: var(--accent);
}
.markdown-document :deep(pre) {
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #f6f8fa;
  overflow: auto;
  overflow-wrap: normal;
}
.markdown-document :deep(code) {
  font-family: ui-monospace, monospace;
  font-size: 13px;
}
.markdown-document :deep(:not(pre) > code) {
  padding: 2px 4px;
  background: #f6f8fa;
  border-radius: 4px;
}
.markdown-document :deep(blockquote) {
  margin-left: 0;
  padding-left: 16px;
  border-left: 3px solid var(--border);
  color: var(--muted);
}
.markdown-document :deep(table) {
  display: block;
  width: max-content;
  max-width: 100%;
  overflow: auto;
  border-collapse: collapse;
}
.markdown-document :deep(th),
.markdown-document :deep(td) {
  padding: 8px 12px;
  border: 1px solid var(--border);
}
.markdown-document :deep(.md-image) {
  color: var(--muted);
}
</style>
