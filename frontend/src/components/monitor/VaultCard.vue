<script setup>
// Vaultwarden 密码库：入口 / 版本 / 响应时延 / 存活
defineProps({
  vault: { type: Object, default: null },
})
</script>

<template>
  <el-card shadow="never" class="mon-card">
    <template #header>
      <div class="ch">
        <span class="ct">Vaultwarden · 密码库</span>
        <el-tag v-if="vault" :type="vault.alive ? 'success' : 'danger'" size="small" effect="light">
          {{ vault.alive ? '在线' : '离线' }}
        </el-tag>
      </div>
    </template>
    <div v-if="vault" class="kv-grid single">
      <div v-if="vault.public_url">
        <span class="k">入口</span>
        <a :href="vault.public_url" target="_blank" rel="noopener" class="vault-link">{{
          vault.public_url
        }}</a>
      </div>
      <div v-if="vault.version"><span class="k">版本</span>{{ vault.version }}</div>
      <div v-if="vault.latency_ms != null">
        <span class="k">响应</span>{{ vault.latency_ms }} ms
      </div>
      <div v-if="vault.error" class="err"><span class="k">错误</span>{{ vault.error }}</div>
    </div>
  </el-card>
</template>

<style scoped>
.mon-card {
  break-inside: avoid;
  margin-bottom: 16px;
}
.ch {
  display: flex;
  align-items: center;
  gap: 10px;
}
.ct {
  font-weight: 600;
}

.kv-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 8px 16px;
  font-size: 0.85rem;
}
.kv-grid .k {
  display: inline-block;
  min-width: 4.5em;
  color: var(--muted);
}
.kv-grid .err {
  color: var(--el-color-danger);
}
.vault-link {
  color: var(--accent);
  text-decoration: none;
  word-break: break-all;
}
.vault-link:hover {
  text-decoration: underline;
}
</style>
