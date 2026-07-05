<script setup>
// 腾讯云轻量服务器 + 本机（CPU/内存/磁盘/月流量包 表盘 + 规格/IP/到期/负载）
import { computed } from 'vue'
import HelpTip from '../HelpTip.vue'
import { useIsMobile } from '../../composables/useIsMobile'
import { fmtBytes, fmtDuration } from '../../utils/format'
import { pct, barColor, shortDate, daysUntil } from './helpers'

const props = defineProps({
  server: { type: Object, default: null },
  lh: { type: Object, default: null },
})

const isMobile = useIsMobile()
const gaugeSize = computed(() => (isMobile.value ? 84 : 96))

// 负载直观化：按核数折算成百分比 + 空闲/较忙/满载 状态词（0.05 这种裸数字太难懂）
const loadInfo = computed(() => {
  const s = props.server
  if (!s?.load || s.load[0] == null) return null
  const cores = s.cpu_count || 1
  const pcts = s.load.map((v) => (v == null ? null : Math.round((v / cores) * 100)))
  const r = s.load[0] / cores
  const level =
    r < 0.7 ? { label: '空闲', type: 'success' }
    : r < 1 ? { label: '较忙', type: 'warning' }
    : { label: '满载', type: 'danger' }
  return { pcts, level, cores }
})

const trafficCycle = computed(() => {
  const t = props.lh?.traffic
  if (!t) return ''
  return `${shortDate(t.start)} → ${shortDate(t.end)}`
})
</script>

<template>
  <el-card shadow="never" class="mon-card">
    <template #header>
      <div class="ch">
        <span class="ct">腾讯云 · 轻量服务器</span>
        <el-tag v-if="lh?.instance" :type="lh.instance.state === 'RUNNING' ? 'success' : 'info'" size="small" effect="light">
          {{ lh.instance.state === 'RUNNING' ? '运行中' : lh.instance.state }}
        </el-tag>
      </div>
    </template>

    <el-alert v-if="lh && !lh.configured" type="info" :closable="false" show-icon
      title="未配置腾讯云只读密钥，仅显示本机指标（流量包/到期信息不可用）" class="mb" />

    <!-- 本机实时 + 月流量包 表盘 -->
    <div v-if="server" class="tiles">
      <div class="tile">
        <div class="tile-l">CPU<span v-if="server.cpu_count" class="muted"> · {{ server.cpu_count }} 核</span></div>
        <el-progress type="dashboard" :width="gaugeSize" :percentage="Math.round(server.cpu_percent)" :color="barColor(server.cpu_percent)" />
      </div>
      <div class="tile">
        <div class="tile-l">内存</div>
        <el-progress type="dashboard" :width="gaugeSize" :percentage="Math.round(server.mem.percent)" :color="barColor(server.mem.percent)" />
        <div class="tile-sub">{{ fmtBytes(server.mem.used) }} / {{ fmtBytes(server.mem.total) }}</div>
      </div>
      <div class="tile">
        <div class="tile-l">磁盘</div>
        <el-progress type="dashboard" :width="gaugeSize" :percentage="Math.round(server.disk.percent)" :color="barColor(server.disk.percent)" />
        <div class="tile-sub">{{ fmtBytes(server.disk.used) }} / {{ fmtBytes(server.disk.total) }}</div>
      </div>
      <div v-if="lh?.traffic" class="tile">
        <div class="tile-l">月流量包</div>
        <el-progress type="dashboard" :width="gaugeSize" :percentage="pct(lh.traffic.used, lh.traffic.total)" :color="barColor(pct(lh.traffic.used, lh.traffic.total))" />
        <div class="tile-sub">{{ fmtBytes(lh.traffic.used) }} / {{ fmtBytes(lh.traffic.total) }}</div>
      </div>
    </div>

    <div v-if="server || lh?.instance" class="kv-grid single">
      <div v-if="lh?.instance"><span class="k">规格</span>{{ lh.instance.cpu }} 核 {{ lh.instance.memory_gb }} GB</div>
      <div v-if="lh?.instance?.ip"><span class="k">公网 IP</span>{{ lh.instance.ip }}</div>
      <div v-if="lh?.instance?.expired_at">
        <span class="k">到期</span>{{ shortDate(lh.instance.expired_at) }}
        <span class="muted">（{{ daysUntil(lh.instance.expired_at) }} 天后）</span>
      </div>
      <div v-if="lh?.traffic">
        <span class="k">流量周期</span>{{ trafficCycle }}
        <span v-if="lh.traffic.overflow > 0" class="err"> · 超额 {{ fmtBytes(lh.traffic.overflow) }}</span>
      </div>
      <div v-if="server"><span class="k">运行</span>{{ fmtDuration(server.uptime) }}</div>
      <div v-if="loadInfo" class="load-cell">
        <span class="k">负载</span>
        <el-tag :type="loadInfo.level.type" size="small" effect="light">{{ loadInfo.level.label }}</el-tag>
        <span class="muted load-pct">{{ loadInfo.pcts[0] }}%</span>
        <HelpTip>
          系统负载 = 正在运行或排队等 CPU/磁盘的任务数，按 {{ loadInfo.cores }} 核折算成百分比，
          低于 100% 表示 CPU 还有余量、超过才会排队变慢。<br />
          近 1 / 5 / 15 分钟：{{ loadInfo.pcts.map((p) => p + '%').join(' / ') }}
        </HelpTip>
      </div>
    </div>
  </el-card>
</template>

<style scoped>
.mon-card { break-inside: avoid; margin-bottom: 16px; }
.mb { margin-bottom: 14px; }
.muted { color: var(--muted); }
.ch { display: flex; align-items: center; gap: 10px; }
.ct { font-weight: 600; }

.tiles {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  margin: 6px 0 16px;
}
.tile { text-align: center; }
.tile-l { font-size: 0.85rem; margin-bottom: 4px; }
.tile-sub { font-size: 0.75rem; color: var(--muted); margin-top: 2px; }

.kv-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 8px 16px;
  font-size: 0.85rem;
}
.kv-grid.single { grid-template-columns: 1fr; }
.kv-grid .k {
  display: inline-block;
  min-width: 4.5em;
  color: var(--muted);
}
.kv-grid .err { color: var(--el-color-danger); }
.load-cell { display: flex; align-items: center; }
.load-pct { margin-left: 8px; }

@media (max-width: 768px) {
  .tiles { justify-content: space-around; gap: 14px 10px; }
  .tile { flex: 0 0 46%; }
}
</style>
