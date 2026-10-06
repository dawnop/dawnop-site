<template>
  <main class="paste-shell">
    <header class="paste-header">
      <span class="paste-brand"><img src="/logo.svg" alt="" />dawnop <span>paste</span></span>
      <nav aria-label="Paste">
        <RouterLink to="/paste">列表</RouterLink><RouterLink to="/paste/new">新建</RouterLink>
      </nav>
    </header>
    <RouterView />
  </main>
</template>
<style>
.paste-shell {
  max-width: 880px;
  margin: 0 auto;
  padding: 40px 24px 64px;
  min-height: 100dvh;
  background: var(--bg);
}
.paste-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 24px;
  margin-bottom: 32px;
  border-bottom: 1px solid var(--border);
}
.paste-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 18px;
}
.paste-brand img {
  width: 24px;
  height: 24px;
}
.paste-brand span {
  font-weight: 400;
  color: var(--muted);
}
.paste-header nav {
  display: flex;
  gap: 20px;
  font-size: 14px;
}
.paste-header a {
  color: var(--muted);
  text-decoration: none;
}
.paste-header a.router-link-exact-active {
  color: var(--accent);
}
.paste-toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 20px;
}
.paste-error {
  color: var(--muted);
  padding: 24px 0;
}
.paste-source {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font:
    13px/1.8 ui-monospace,
    monospace;
}
.paste-meta {
  color: var(--muted);
  font-size: 12px;
}
.paste-shell .el-pagination {
  margin-top: 24px;
  justify-content: center;
}
@media (max-width: 600px) {
  .paste-shell {
    padding: 24px 16px 40px;
  }
  .paste-header {
    margin-bottom: 24px;
  }
}
</style>
