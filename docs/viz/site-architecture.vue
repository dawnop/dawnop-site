<script setup>
import { ref, computed } from 'vue'

// 六个节点：浏览器 / Nginx / FastAPI / SQLite / 七牛 / CDN
const nodes = {
  browser: { x: 40, y: 128, w: 104, h: 48, label: '浏览器', sub: '你' },
  nginx: { x: 250, y: 128, w: 104, h: 48, label: 'Nginx', sub: ':443 源站' },
  api: { x: 452, y: 54, w: 120, h: 48, label: 'FastAPI', sub: 'uvicorn' },
  db: { x: 646, y: 54, w: 104, h: 48, label: 'SQLite', sub: '一个文件' },
  qiniu: { x: 452, y: 202, w: 120, h: 48, label: '七牛 Kodo', sub: '私有空间' },
  cdn: { x: 646, y: 202, w: 104, h: 48, label: 'CDN', sub: 'cdn.dawnop' },
}

// 边：id + 两端节点 + 是否虚线（虚线=浏览器绕过后端的直连）
const edges = {
  bn: { a: 'browser', b: 'nginx' },
  na: { a: 'nginx', b: 'api' },
  ad: { a: 'api', b: 'db' },
  aq: { a: 'api', b: 'qiniu' },
  bq: { a: 'browser', b: 'qiniu', dash: true },
  bc: { a: 'browser', b: 'cdn', dash: true },
  cn: { a: 'cdn', b: 'nginx', dash: true },
}

const scenarios = [
  {
    key: 'article',
    label: '读一篇文章',
    lit: ['bn', 'na', 'ad'],
    desc: '请求页面和文章 JSON：Nginx 反代给 FastAPI，查一次 SQLite 就返回。字节量小，整条路都在源站内。',
  },
  {
    key: 'image',
    label: '看网盘里的图',
    lit: ['bn', 'na', 'bq'],
    desc: 'FastAPI 只做一件事：验证你登录了，回一个 302 签名地址。之后浏览器直连七牛取图片字节，后端不碰文件流。',
  },
  {
    key: 'upload',
    label: '上传一个文件',
    lit: ['bn', 'na', 'bq', 'aq'],
    desc: '先向后端要一次性上传凭证（SecretKey 不出后端），浏览器拿着凭证把字节直传七牛；传完再登记，后端顺便 stat 校验对象真存在。',
  },
  {
    key: 'asset',
    label: '加载页面资源',
    lit: ['bc', 'cn'],
    desc: 'JS / CSS / 字体走 CDN 域名：命中边缘缓存就地返回；没命中才回源到 Nginx 拉一次、缓存下来。',
  },
]

const active = ref('article')
const cur = computed(() => scenarios.find((s) => s.key === active.value))
const litEdges = computed(() => new Set(cur.value.lit))
const litNodes = computed(() => {
  const set = new Set()
  for (const id of cur.value.lit) {
    set.add(edges[id].a)
    set.add(edges[id].b)
  }
  return set
})

const cx = (n) => n.x + n.w / 2
const cy = (n) => n.y + n.h / 2

// 两节点间连线的起止点（从矩形边缘出发，稍微好看点）
function line(id) {
  const e = edges[id]
  const A = nodes[e.a]
  const B = nodes[e.b]
  return { x1: cx(A), y1: cy(A), x2: cx(B), y2: cy(B) }
}
</script>

<template>
  <div class="arch">
    <div class="chips">
      <button
        v-for="s in scenarios"
        :key="s.key"
        class="chip"
        :class="{ on: active === s.key }"
        @click="active = s.key"
      >
        {{ s.label }}
      </button>
    </div>

    <svg viewBox="0 0 780 288" class="canvas">
      <!-- 边 -->
      <g>
        <line
          v-for="(e, id) in edges"
          :key="id"
          v-bind="line(id)"
          class="edge"
          :class="{ lit: litEdges.has(id), dash: e.dash }"
        />
      </g>
      <!-- 节点 -->
      <g v-for="(n, id) in nodes" :key="id">
        <rect
          :x="n.x"
          :y="n.y"
          :width="n.w"
          :height="n.h"
          rx="10"
          class="node"
          :class="{ lit: litNodes.has(id) }"
        />
        <text :x="cx(n)" :y="cy(n) - 4" class="n-label" :class="{ lit: litNodes.has(id) }">
          {{ n.label }}
        </text>
        <text :x="cx(n)" :y="cy(n) + 12" class="n-sub">{{ n.sub }}</text>
      </g>
    </svg>

    <p class="desc">{{ cur.desc }}</p>
    <p class="hint">灰色虚线 = 浏览器绕过后端、直连七牛/CDN 取字节（省服务器流量）。</p>
  </div>
</template>

<style scoped>
.arch {
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #1f2328;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.chip {
  border: 1px solid #d0d7de;
  background: #fff;
  color: #57606a;
  border-radius: 20px;
  padding: 5px 14px;
  font-size: 0.86rem;
  cursor: pointer;
  transition: all 0.15s;
}
.chip:hover {
  border-color: #1677ff;
  color: #1677ff;
}
.chip.on {
  background: #1677ff;
  border-color: #1677ff;
  color: #fff;
}
.canvas {
  width: 100%;
  height: auto;
  display: block;
}
.edge {
  stroke: #d8dee4;
  stroke-width: 2;
  transition: stroke 0.25s;
}
.edge.dash {
  stroke-dasharray: 5 4;
}
.edge.lit {
  stroke: #1677ff;
  stroke-width: 3;
}
.node {
  fill: #fff;
  stroke: #d0d7de;
  stroke-width: 1.5;
  transition: all 0.25s;
}
.node.lit {
  stroke: #1677ff;
  fill: #f0f6ff;
}
.n-label {
  text-anchor: middle;
  font-size: 14px;
  font-weight: 600;
  fill: #57606a;
  transition: fill 0.25s;
}
.n-label.lit {
  fill: #1677ff;
}
.n-sub {
  text-anchor: middle;
  font-size: 10.5px;
  fill: #9aa0a6;
}
.desc {
  margin: 12px 0 0;
  font-size: 0.92rem;
  line-height: 1.7;
  color: #3c4149;
  min-height: 3.4em;
}
.hint {
  margin: 8px 0 0;
  font-size: 0.8rem;
  color: #9aa0a6;
}
</style>
