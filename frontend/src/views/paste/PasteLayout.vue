<script setup>
import { Plus } from '@element-plus/icons-vue'
import ToolLayout from '../../components/ToolLayout.vue'
</script>

<template>
  <ToolLayout name="剪贴板" class="paste-shell">
    <template #nav>
      <nav class="paste-nav" aria-label="剪贴板">
        <RouterLink to="/paste" class="paste-nav-link">列表</RouterLink>
        <RouterLink to="/paste/new" class="tool-primary"
          ><el-icon><Plus /></el-icon>新建</RouterLink
        >
      </nav>
    </template>
    <RouterView />
  </ToolLayout>
</template>
<style>
.paste-nav {
  display: flex;
  align-items: center;
  gap: 28px;
  font-size: 14px;
}
.paste-nav-link {
  color: var(--muted);
  text-decoration: none;
}
.paste-nav-link:hover,
.paste-nav-link.router-link-exact-active {
  color: var(--accent);
}
.paste-meta {
  color: var(--muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.paste-state a {
  font-size: 14px;
  text-decoration: none;
}
.paste-tabs {
  display: inline-flex;
  gap: 4px;
  padding: 3px;
  border-radius: 6px;
  background: var(--el-fill-color-light);
}
.paste-tabs button {
  min-width: 56px;
  padding: 5px 12px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 13px;
  line-height: 1.5;
  cursor: pointer;
}
.paste-tabs button[aria-pressed='true'] {
  background: var(--bg);
  color: var(--accent);
  box-shadow: 0 1px 3px #0000000d;
}
.paste-tabs button:disabled {
  cursor: wait;
}
.paste-source {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font:
    13px/1.9 ui-monospace,
    SFMono-Regular,
    Consolas,
    monospace;
  tab-size: 2;
}
@media (max-width: 760px) {
  .paste-nav {
    gap: 18px;
  }
}
@media (max-width: 380px) {
  .paste-nav {
    gap: 12px;
  }
  .paste-nav .tool-primary {
    padding: 9px 11px;
  }
}
</style>
