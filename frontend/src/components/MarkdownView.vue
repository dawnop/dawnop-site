<script setup>
import { computed, ref, watch, onMounted, nextTick } from 'vue'
import MarkdownIt from 'markdown-it'
import { katex } from '@mdit/plugin-katex'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'
import 'katex/dist/katex.min.css'

const props = defineProps({
  source: { type: String, default: '' },
})
const emit = defineEmits(['headings'])

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

// 把标题文本转成 url 友好的 id（保留中英文数字）
function slugify(text) {
  return (
    text
      .trim()
      .toLowerCase()
      .replace(/[\s]+/g, '-')
      .replace(/[^\w一-鿿-]/g, '') || 'h'
  )
}

// 渲染一次产出 html 与目录（headings）：给 h1~h3 注入唯一 id
function render(src) {
  const tokens = md.parse(src || '', {})
  const headings = []
  const seen = {}
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i]
    if (t.type !== 'heading_open') continue
    const level = Number(t.tag.slice(1))
    if (level > 3) continue
    const text = (tokens[i + 1] && tokens[i + 1].content) || ''
    let id = slugify(text)
    seen[id] = (seen[id] || 0) + 1
    if (seen[id] > 1) id += '-' + seen[id]
    t.attrSet('id', id)
    headings.push({ id, text, level })
  }
  return { html: md.renderer.render(tokens, md.options, {}), headings }
}

const result = computed(() => render(props.source))
const html = computed(() => result.value.html)

watch(
  () => result.value.headings,
  (h) => emit('headings', h),
  { immediate: true }
)

// 渲染后增强 DOM：标题悬停锚点 + 代码块复制按钮
const root = ref(null)
function enhance() {
  const el = root.value
  if (!el) return
  el.querySelectorAll('h1[id], h2[id], h3[id]').forEach((h) => {
    if (h.querySelector('.heading-anchor')) return
    const a = document.createElement('a')
    a.className = 'heading-anchor'
    a.href = '#' + h.id
    a.title = '复制链接'
    a.setAttribute('aria-label', '复制本节链接')
    a.textContent = '#'
    h.prepend(a)
  })
  el.querySelectorAll('pre.hljs').forEach((pre) => {
    if (pre.querySelector('.code-copy')) return
    const btn = document.createElement('button')
    btn.className = 'code-copy'
    btn.type = 'button'
    btn.textContent = '复制'
    pre.appendChild(btn)
  })
}
// 复制到剪贴板：安全上下文(HTTPS/localhost)用 Clipboard API；
// 普通 HTTP 下 navigator.clipboard 不存在，回退到 execCommand。返回是否成功，绝不抛错。
function copyText(text) {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text).then(
      () => true,
      () => false
    )
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return Promise.resolve(ok)
  } catch (e) {
    return Promise.resolve(false)
  }
}

function onClick(e) {
  // 标题锚点：点击复制本节直达链接（/path#标题）到剪贴板并滚动过去，给「✓」反馈
  const anchor = e.target.closest('.heading-anchor')
  if (anchor) {
    e.preventDefault()
    const id = decodeURIComponent(anchor.getAttribute('href').slice(1))
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
    const url =
      location.origin + location.pathname + location.search + '#' + encodeURIComponent(id)
    copyText(url).then((ok) => {
      if (!ok) return
      anchor.textContent = '✓'
      setTimeout(() => (anchor.textContent = '#'), 1200)
    })
    return
  }
  const btn = e.target.closest('.code-copy')
  if (!btn) return
  const code = btn.parentElement.querySelector('code')
  if (!code) return
  copyText(code.innerText).then((ok) => {
    btn.textContent = ok ? '已复制' : '复制失败'
    setTimeout(() => (btn.textContent = '复制'), 1500)
  })
}

onMounted(() => nextTick(enhance))
watch(html, () => nextTick(enhance))
</script>

<template>
  <div ref="root" class="markdown-body" @click="onClick" v-html="html"></div>
</template>

<style scoped>
.markdown-body {
  line-height: 1.8;
  word-wrap: break-word;
  font-size: 1.02rem;
  color: #24292f;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  position: relative;
  margin: 1.8em 0 0.7em;
  line-height: 1.35;
  font-weight: 650;
  scroll-margin-top: 80px;
}
.markdown-body :deep(h2) {
  padding-bottom: 0.3em;
  border-bottom: 1px solid var(--border);
}
/* 标题悬停锚点 */
.markdown-body :deep(.heading-anchor) {
  position: absolute;
  left: -1.1em;
  color: var(--border);
  text-decoration: none;
  opacity: 0;
  transition: opacity 0.15s;
  font-weight: 400;
}
.markdown-body :deep(h1:hover) .heading-anchor,
.markdown-body :deep(h2:hover) .heading-anchor,
.markdown-body :deep(h3:hover) .heading-anchor {
  opacity: 1;
  color: var(--accent);
}
.markdown-body :deep(p) {
  margin: 1.1em 0;
}
.markdown-body :deep(a) {
  color: var(--accent);
  text-decoration: none;
  border-bottom: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
}
.markdown-body :deep(a:hover) {
  border-bottom-color: var(--accent);
}
/* 代码块 */
.markdown-body :deep(pre.hljs) {
  position: relative;
  padding: 1em 1.1em;
  border-radius: 10px;
  overflow: auto;
  background: #f6f8fa;
  border: 1px solid var(--border);
}
.markdown-body :deep(.code-copy) {
  position: absolute;
  top: 8px;
  right: 8px;
  padding: 2px 10px;
  font-size: 0.75rem;
  color: var(--muted);
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 6px;
  opacity: 0;
  transition: opacity 0.15s;
}
.markdown-body :deep(pre.hljs:hover) .code-copy {
  opacity: 1;
}
.markdown-body :deep(.code-copy:hover) {
  color: var(--accent);
  border-color: var(--accent);
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
  margin: 1.2em 0;
  padding: 0.4em 1.1em;
  color: var(--muted);
  border-left: 3px solid var(--accent);
  background: #fafbfc;
  border-radius: 0 8px 8px 0;
}
.markdown-body :deep(blockquote p) {
  margin: 0.4em 0;
}
.markdown-body :deep(img) {
  max-width: 100%;
  border-radius: 8px;
}
.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid var(--border);
  margin: 2em 0;
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 1.4em;
}
.markdown-body :deep(li) {
  margin: 0.4em 0;
}
.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 1.2em 0;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--border);
  padding: 0.5em 0.9em;
}
.markdown-body :deep(thead th) {
  background: #f6f8fa;
}
</style>
