<script setup>
// Drop 和 Paste 共用页面尺度，功能区通过插槽提供。
defineProps({ name: { type: String, required: true } })
</script>

<template>
  <div class="tool-page">
    <header class="tool-header">
      <span class="tool-brand">
        <img src="/logo.svg" alt="" width="26" height="26" />
        <span>dawnop</span><span class="tool-name">{{ name }}</span>
      </span>
      <slot name="nav" />
    </header>
    <main class="tool-main"><slot /></main>
  </div>
</template>

<style>
.tool-page {
  --tool-panel-padding: 28px;
  --tool-heading-gap: 32px;
  min-height: 100dvh;
  padding: 0 32px;
  background: var(--bg);
  color: var(--fg);
}
.tool-header {
  max-width: 1040px;
  height: 88px;
  margin: 0 auto;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.tool-brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-size: 20px;
  font-weight: 650;
  letter-spacing: -0.5px;
  white-space: nowrap;
}
.tool-brand img {
  flex: none;
}
.tool-brand .tool-name {
  font-weight: 400;
  color: var(--muted);
}
.tool-main {
  width: 100%;
  max-width: 960px;
  margin: 0 auto;
  padding: 54px 0 64px;
}
.tool-page .tool-heading {
  margin: 0;
  font-size: clamp(26px, 3.3vw, 36px);
  line-height: 1.35;
  font-weight: 600;
  letter-spacing: -0.8px;
  color: var(--fg);
  overflow-wrap: anywhere;
}
.tool-page .tool-panel {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--el-border-radius-base);
}
.tool-page .tool-primary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  height: 40px;
  padding: 9px 15px;
  border: 0;
  border-radius: var(--el-border-radius-base);
  background: var(--accent);
  color: var(--bg);
  font: inherit;
  font-size: var(--el-font-size-base);
  line-height: 1.5;
  text-decoration: none;
  cursor: pointer;
}
.tool-page .tool-primary:hover {
  background: var(--accent-hover);
}
.tool-page .tool-primary:disabled {
  background: var(--el-color-primary-light-5);
  cursor: not-allowed;
}
.tool-page .tool-state {
  max-width: 540px;
  margin: 42px auto 0;
  padding: 40px 32px;
  text-align: center;
}
.tool-state h1,
.tool-state h2 {
  margin: 0 0 12px;
  font-size: 24px;
  font-weight: 600;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.tool-state p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  overflow-wrap: anywhere;
}
.tool-state > a,
.tool-state > .el-button {
  margin-top: 22px;
}
.tool-state > .el-icon {
  display: block;
  margin: 28px auto 18px;
  font-size: 42px;
  color: var(--accent);
}
.tool-page a:focus-visible,
.tool-page button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 3px;
}
@media (max-width: 760px) {
  .tool-page {
    --tool-panel-padding: 20px;
    --tool-heading-gap: 24px;
    padding: 0 20px;
  }
  .tool-header {
    height: 70px;
  }
  .tool-main {
    padding: 32px 0 40px;
  }
  .tool-page .tool-state {
    margin-top: 16px;
    padding: 32px 20px;
  }
}
@media (max-width: 380px) {
  .tool-page {
    --tool-panel-padding: 16px;
    padding: 0 12px;
  }
  .tool-header {
    gap: 12px;
  }
}
</style>
