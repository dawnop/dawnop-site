<script setup>
import MarkdownView from '../components/MarkdownView.vue'

// 关于页内容（可按需改为从后端文章读取）
const about = `## 关于

你好，这里是 **dawnop** 的个人博客。

- 记录技术、思考与笔记
- 文章支持 Markdown 与 LaTeX 公式，如 $e^{i\\pi} + 1 = 0$

联系：dawnlulucifer@gmail.com
`
</script>

<template>
  <MarkdownView :source="about" />
</template>
