<!-- viz-name: 关于页时间线 -->
<script setup>
// 关于页的经历时间线（扁平：灰线 + 圆点，仅「现在」用一点蓝）。
// 纯呈现组件：所有文案由 markdown 的 ```viz 围栏参数传入（见 viz/fence.js），组件本身不写死内容。
// 参数形状：
//   nodes:   [{ phase, title, current?, paras: [{ text, link?: { text, href } }] }]
//   closing: string
// 不引入 vue 之外的任何依赖。改了模板/样式后需重新编译（见 docs/viz 说明）；改文案只动 markdown。
defineProps({
  nodes: { type: Array, default: () => [] },
  closing: { type: String, default: '' },
})
</script>

<template>
  <div class="tl">
    <ol class="tl-list">
      <li
        v-for="(n, i) in nodes"
        :key="i"
        class="tl-item"
        :class="{ current: n.current }"
      >
        <div class="tl-phase">{{ n.phase }}</div>
        <div class="tl-title">{{ n.title }}</div>
        <p v-for="(pa, j) in n.paras" :key="j" class="tl-body">
          {{ pa.text
          }}<a
            v-if="pa.link"
            class="tl-link"
            :href="pa.link.href"
            target="_blank"
            rel="noopener"
            >{{ pa.link.text }} ↗</a
          >
        </p>
      </li>
    </ol>
    <p v-if="closing" class="tl-closing">{{ closing }}</p>
  </div>
</template>

<style scoped>
.tl {
  margin: 4px 0;
}
.tl-list {
  list-style: none;
  margin: 0;
  padding: 0;
  position: relative;
}
/* 贯穿的竖线 */
.tl-list::before {
  content: '';
  position: absolute;
  left: 5px;
  top: 8px;
  bottom: 8px;
  width: 2px;
  background: var(--border, #ebedf0);
}
.tl-item {
  position: relative;
  padding: 0 0 30px 30px;
}
.tl-item:last-child {
  padding-bottom: 0;
}
/* 节点圆点 */
.tl-item::before {
  content: '';
  position: absolute;
  left: 0;
  top: 5px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #fff;
  border: 2px solid var(--border, #d0d7de);
  box-sizing: border-box;
}
.tl-item.current::before {
  border-color: var(--accent, #1677ff);
  background: var(--accent, #1677ff);
}
.tl-phase {
  font-size: 0.8rem;
  letter-spacing: 0.04em;
  color: #8b949e;
}
.tl-title {
  margin: 3px 0 0;
  font-size: 1.08rem;
  font-weight: 650;
  line-height: 1.45;
  color: var(--fg, #1a1a1a);
}
.tl-body {
  margin: 9px 0 0;
  font-size: 0.98rem;
  line-height: 1.8;
  color: var(--muted, #57606a);
}
.tl-link {
  margin-left: 6px;
  white-space: nowrap;
  color: var(--accent, #1677ff);
  text-decoration: none;
  border-bottom: 1px solid color-mix(in srgb, var(--accent, #1677ff) 35%, transparent);
}
.tl-link:hover {
  border-bottom-color: var(--accent, #1677ff);
}
.tl-closing {
  margin: 28px 0 0;
  padding-top: 22px;
  border-top: 1px solid var(--border, #ebedf0);
  font-size: 0.98rem;
  line-height: 1.8;
  color: var(--muted, #57606a);
}
</style>
