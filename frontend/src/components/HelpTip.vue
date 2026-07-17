<script setup>
// 圆问号提示：把字段的解释性灰字收进一个 ? 图标，hover 出 tooltip，保持表单清爽。
// 用法：<HelpTip>访问地址 <code>/article/xxx</code></HelpTip>
import { QuestionFilled } from '@element-plus/icons-vue'
</script>

<template>
  <el-tooltip placement="top" :show-after="60">
    <el-icon class="help-tip"><QuestionFilled /></el-icon>
    <template #content
      ><span class="help-tip-content"><slot /></span
    ></template>
  </el-tooltip>
</template>

<style scoped>
.help-tip {
  color: #b6bcc6;
  cursor: help;
  font-size: 14px;
  vertical-align: -2px;
  margin-left: 4px;
}
.help-tip:hover {
  color: var(--accent);
}
.help-tip-content {
  line-height: 1.7;
}
</style>
