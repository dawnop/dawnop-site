<script setup>
import { Plus } from '@element-plus/icons-vue'
</script>

<template>
  <div class="paste-shell">
    <header class="paste-header">
      <div class="paste-header-inner">
        <span class="paste-brand"><img src="/logo.svg" alt="" />dawnop <span>paste</span></span>
        <nav aria-label="Paste">
          <RouterLink to="/paste" class="paste-nav-link">列表</RouterLink>
          <RouterLink to="/paste/new" class="paste-new-link">
            <el-icon><Plus /></el-icon>新建
          </RouterLink>
        </nav>
      </div>
    </header>
    <main class="paste-main"><RouterView /></main>
  </div>
</template>

<style>
.paste-shell {
  min-height: 100dvh;
  background: var(--bg);
}
.paste-header {
  border-bottom: 1px solid var(--border);
}
.paste-header-inner {
  max-width: 1088px;
  height: 80px;
  margin: 0 auto;
  padding: 0 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.paste-brand {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  font-size: 18px;
  font-weight: 600;
  letter-spacing: -0.3px;
}
.paste-brand img {
  width: 24px;
  height: 24px;
}
.paste-brand span {
  margin-left: 2px;
  color: var(--muted);
  font-weight: 400;
}
.paste-header nav {
  display: flex;
  align-items: center;
  gap: 28px;
  font-size: 14px;
}
.paste-header a {
  text-decoration: none;
}
.paste-nav-link {
  color: var(--muted);
}
.paste-nav-link:hover,
.paste-nav-link.router-link-exact-active {
  color: var(--accent);
}
.paste-new-link {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 36px;
  padding: 0 15px;
  border-radius: var(--el-border-radius-base);
  background: var(--accent);
  color: white;
  transition: background 0.15s;
}
.paste-new-link:hover {
  background: var(--accent-hover);
}
.paste-main {
  max-width: 1088px;
  margin: 0 auto;
  padding: 48px 32px 72px;
}
.paste-meta {
  color: var(--muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.paste-heading {
  margin: 0;
  font-size: 28px;
  line-height: 1.4;
  font-weight: 600;
  letter-spacing: -0.6px;
}
.paste-state {
  display: flex;
  min-height: 300px;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 16px;
  padding: 48px 20px;
  text-align: center;
  border: 1px solid var(--border);
  border-radius: var(--el-border-radius-base);
}
.paste-state > .el-icon {
  color: var(--accent);
  font-size: 32px;
}
.paste-state h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 500;
}
.paste-state p {
  margin: 0;
  color: var(--muted);
  font-size: 14px;
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
.paste-shell a:focus-visible,
.paste-shell button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 3px;
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
@media (max-width: 600px) {
  .paste-header-inner {
    height: 68px;
    padding: 0 20px;
  }
  .paste-header nav {
    gap: 18px;
  }
  .paste-main {
    padding: 32px 20px 48px;
  }
  .paste-heading {
    font-size: 24px;
  }
}
@media (max-width: 380px) {
  .paste-header-inner {
    padding: 0 16px;
  }
  .paste-main {
    padding: 28px 16px 40px;
  }
  .paste-header nav {
    gap: 12px;
  }
  .paste-new-link {
    padding: 0 11px;
  }
}
</style>
