<script setup>
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import { katex } from '@mdit/plugin-katex'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'

const props = defineProps({
  source: { type: String, default: '' },
})

// markdown-it + KaTeX（LaTeX，与后台编辑器同款）+ highlight.js（代码高亮）
const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  highlight(str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return (
          '<pre class="hljs"><code>' +
          hljs.highlight(str, { language: lang }).value +
          '</code></pre>'
        )
      } catch (e) {
        /* 回退到转义 */
      }
    }
    return '<pre class="hljs"><code>' + md.utils.escapeHtml(str) + '</code></pre>'
  },
}).use(katex)

const html = computed(() => md.render(props.source || ''))
</script>

<template>
  <div class="markdown-body" v-html="html"></div>
</template>

<style scoped>
.markdown-body {
  line-height: 1.75;
  word-wrap: break-word;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  margin: 1.6em 0 0.6em;
  line-height: 1.3;
}
.markdown-body :deep(p) {
  margin: 0.9em 0;
}
.markdown-body :deep(pre.hljs) {
  padding: 1em;
  border-radius: 8px;
  overflow: auto;
  background: #f6f8fa;
}
.markdown-body :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.9em;
}
.markdown-body :deep(:not(pre) > code) {
  background: #f0f1f3;
  padding: 0.15em 0.4em;
  border-radius: 4px;
}
.markdown-body :deep(blockquote) {
  margin: 1em 0;
  padding: 0.2em 1em;
  color: #57606a;
  border-left: 4px solid #d0d7de;
}
.markdown-body :deep(img) {
  max-width: 100%;
}
.markdown-body :deep(table) {
  border-collapse: collapse;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #d0d7de;
  padding: 0.4em 0.8em;
}
</style>
