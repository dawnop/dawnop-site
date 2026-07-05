<script setup>
import { ref, computed, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { monitorApi } from '../../api'
import MiniChart from '../../components/MiniChart.vue'

const data = ref(null)
const loading = ref(true)
const refreshing = ref(false)
const updatedAt = ref('')

async function load(refresh = false) {
  refresh ? (refreshing.value = true) : (loading.value = true)
  try {
    const { data: d } = await monitorApi.overview(refresh)
    data.value = d
    updatedAt.value = new Date().toLocaleTimeString('zh-CN')
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
    refreshing.value = false
  }
}
onMounted(() => load(false))

// ---- 格式化 ----
function fmtBytes(n) {
  n = Number(n) || 0
  if (n < 1024) return n + ' B'
  const u = ['KB', 'MB', 'GB', 'TB']
  let i = -1
  do { n /= 1024; i++ } while (n >= 1024 && i < u.length - 1)
  return n.toFixed(n >= 100 || i === 0 ? 0 : n >= 10 ? 1 : 2) + ' ' + u[i]
}
function fmtDuration(sec) {
  sec = Number(sec) || 0
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (d) return `${d} 天 ${h} 小时`
  if (h) return `${h} 小时 ${m} 分`
  return `${m} 分`
}
function daysUntil(iso) {
  if (!iso) return null
  return Math.ceil((new Date(iso) - Date.now()) / 86400000)
}
function shortDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.getMonth() + 1}/${d.getDate()}`
}
function dateYmd(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}
function pct(used, total) {
  total = Number(total) || 0
  if (!total) return 0
  return Math.min(100, Math.round((Number(used) / total) * 1000) / 10)
}
function barColor(p) {
  return p >= 90 ? '#cf1322' : p >= 70 ? '#d48806' : '#1677ff'
}
function labelFromUnix(t) {
  const d = new Date(t * 1000)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

// ---- 分块取数（容错） ----
const server = computed(() => data.value?.server || null)
const lh = computed(() => data.value?.lighthouse || null)
const qn = computed(() => data.value?.qiniu || null)
const vault = computed(() => data.value?.vault || null)

const cpuTrend = computed(() =>
  (lh.value?.cpu_trend || []).map((p) => ({ label: labelFromUnix(p.t), value: p.v }))
)
const spaceTrend = computed(() =>
  (qn.value?.space_trend || []).map((p) => ({ label: labelFromUnix(p.t), value: p.v }))
)
const flowTrend = computed(() =>
  (qn.value?.flow_trend || []).map((p) => ({ label: shortDate(p.t), value: p.v }))
)
const cdnTrend = computed(() =>
  (qn.value?.cdn_trend || []).map((p) => ({ label: labelFromUnix(p.t), value: p.v }))
)
function fmtMbps(bps) {
  return ((Number(bps) || 0) / 1e6).toFixed(2) + ' Mbps'
}

const trafficCycle = computed(() => {
  const t = lh.value?.traffic
  if (!t) return ''
  return `${shortDate(t.start)} → ${shortDate(t.end)}`
})
</script>

<template>
  <div v-loading="loading">
    <div class="page-head">
      <p class="hint">
        资源用量总览 · 三方数据缓存约 2 分钟
        <span v-if="updatedAt" class="muted">（{{ updatedAt }} 更新）</span>
      </p>
      <el-button :icon="Refresh" :loading="refreshing" @click="load(true)">刷新</el-button>
    </div>

    <div v-if="data" class="cards">
      <!-- 腾讯云 Lighthouse + 本机 -->
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

        <!-- 流量包 -->
        <div v-if="lh?.traffic" class="block">
          <div class="block-t">
            <span>本月流量包</span>
            <span class="muted">{{ trafficCycle }}</span>
          </div>
          <el-progress
            :percentage="pct(lh.traffic.used, lh.traffic.total)"
            :color="barColor(pct(lh.traffic.used, lh.traffic.total))"
            :stroke-width="10"
          />
          <div class="kv-row">
            <span>已用 <b>{{ fmtBytes(lh.traffic.used) }}</b> / {{ fmtBytes(lh.traffic.total) }}</span>
            <span>剩余 {{ fmtBytes(lh.traffic.remaining) }}</span>
            <el-tag v-if="lh.traffic.overflow > 0" type="danger" size="small">超额 {{ fmtBytes(lh.traffic.overflow) }}</el-tag>
          </div>
        </div>

        <!-- 本机实时 -->
        <div v-if="server" class="tiles">
          <div class="tile">
            <div class="tile-l">CPU<span v-if="server.cpu_count" class="muted"> · {{ server.cpu_count }} 核</span></div>
            <el-progress type="dashboard" :width="76" :percentage="Math.round(server.cpu_percent)" :color="barColor(server.cpu_percent)" />
          </div>
          <div class="tile">
            <div class="tile-l">内存</div>
            <el-progress type="dashboard" :width="76" :percentage="Math.round(server.mem.percent)" :color="barColor(server.mem.percent)" />
            <div class="tile-sub">{{ fmtBytes(server.mem.used) }} / {{ fmtBytes(server.mem.total) }}</div>
          </div>
          <div class="tile">
            <div class="tile-l">磁盘</div>
            <el-progress type="dashboard" :width="76" :percentage="Math.round(server.disk.percent)" :color="barColor(server.disk.percent)" />
            <div class="tile-sub">{{ fmtBytes(server.disk.used) }} / {{ fmtBytes(server.disk.total) }}</div>
          </div>
        </div>

        <div v-if="server || lh?.instance" class="kv-grid">
          <div v-if="lh?.instance"><span class="k">规格</span>{{ lh.instance.cpu }} 核 {{ lh.instance.memory_gb }} GB</div>
          <div v-if="lh?.instance?.ip"><span class="k">公网 IP</span>{{ lh.instance.ip }}</div>
          <div v-if="lh?.instance?.expired_at">
            <span class="k">到期</span>{{ shortDate(lh.instance.expired_at) }}
            <span class="muted">（{{ daysUntil(lh.instance.expired_at) }} 天后）</span>
          </div>
          <div v-if="server"><span class="k">运行</span>{{ fmtDuration(server.uptime) }}</div>
          <div v-if="server?.load?.[0] != null"><span class="k">负载</span>{{ server.load.map((x) => x?.toFixed(2)).join(' / ') }}</div>
          <div v-if="server"><span class="k">网络累计</span>↑{{ fmtBytes(server.net.sent) }} ↓{{ fmtBytes(server.net.recv) }}</div>
        </div>

        <div v-if="cpuTrend.length" class="trend">
          <div class="trend-t">CPU 使用率 · 近 30 天</div>
          <MiniChart :points="cpuTrend" :format="(v) => v.toFixed(1) + ' %'" />
        </div>
      </el-card>

      <!-- 七牛 Kodo -->
      <el-card shadow="never" class="mon-card">
        <template #header>
          <div class="ch">
            <span class="ct">七牛 Kodo · 对象存储</span>
            <span v-if="qn?.bucket" class="muted">{{ qn.bucket }}</span>
          </div>
        </template>

        <el-alert v-if="qn && !qn.configured" type="info" :closable="false" show-icon title="未配置七牛密钥" />
        <el-alert v-else-if="qn?.error" type="warning" :closable="false" show-icon :title="'七牛统计获取失败：' + qn.error" />

        <template v-else-if="qn">
          <div class="block">
            <div class="block-t">
              <span>存储用量</span>
              <span class="muted">配额 {{ fmtBytes(qn.quota) }}</span>
            </div>
            <el-progress :percentage="pct(qn.space, qn.quota)" :color="barColor(pct(qn.space, qn.quota))" :stroke-width="10" />
            <div class="kv-row"><span>已用 <b>{{ fmtBytes(qn.space) }}</b></span></div>
          </div>

          <div v-if="qn.cdn_pack" class="block">
            <div class="block-t">
              <span>CDN 流量包</span>
              <span class="muted">
                总 {{ fmtBytes(qn.cdn_pack.total) }}
                <template v-if="qn.cdn_pack.expire"> · 有效期至 {{ dateYmd(qn.cdn_pack.expire) }}</template>
              </span>
            </div>
            <el-progress
              :percentage="pct(qn.cdn_pack.used, qn.cdn_pack.total)"
              :color="barColor(pct(qn.cdn_pack.used, qn.cdn_pack.total))"
              :stroke-width="10"
            />
            <div class="kv-row">
              <span>已用 <b>{{ fmtBytes(qn.cdn_pack.used) }}</b> · 剩余 {{ fmtBytes(qn.cdn_pack.remain) }}</span>
              <span v-if="qn.cdn_peak_bps">峰值带宽 {{ fmtMbps(qn.cdn_peak_bps) }}</span>
              <span v-if="qn.cdn_domains?.length" class="muted">{{ qn.cdn_domains.join('、') }}</span>
            </div>
          </div>
          <el-alert v-if="qn.cdn_error" type="warning" :closable="false" show-icon
            :title="'CDN 用量获取失败：' + qn.cdn_error" class="mb" />

          <div class="stat-row">
            <div class="stat"><div class="stat-v">{{ qn.count }}</div><div class="stat-l">文件对象</div></div>
            <div class="stat"><div class="stat-v">{{ fmtBytes(qn.flow_30d) }}</div><div class="stat-l">源站流出 · 30 天</div></div>
            <div class="stat"><div class="stat-v">{{ qn.hits_30d }}</div><div class="stat-l">请求次数 · 30 天</div></div>
          </div>

          <div class="trend">
            <div class="trend-t">CDN 流量 · 近 30 天</div>
            <MiniChart :points="cdnTrend" color="#722ed1" :format="fmtBytes" />
          </div>
          <div class="trend">
            <div class="trend-t">存储量 · 近 30 天</div>
            <MiniChart :points="spaceTrend" color="#389e0d" :format="fmtBytes" />
          </div>
          <div class="trend">
            <div class="trend-t">源站流出流量 · 近 30 天</div>
            <MiniChart :points="flowTrend" color="#d48806" :format="fmtBytes" />
          </div>
        </template>
      </el-card>

      <!-- Vaultwarden -->
      <el-card shadow="never" class="mon-card">
        <template #header>
          <div class="ch">
            <span class="ct">Vaultwarden · 密码库</span>
            <el-tag v-if="vault" :type="vault.alive ? 'success' : 'danger'" size="small" effect="light">
              {{ vault.alive ? '在线' : '离线' }}
            </el-tag>
          </div>
        </template>
        <div v-if="vault" class="kv-grid">
          <div><span class="k">地址</span>{{ vault.url }}</div>
          <div v-if="vault.version"><span class="k">版本</span>{{ vault.version }}</div>
          <div v-if="vault.latency_ms != null"><span class="k">响应</span>{{ vault.latency_ms }} ms</div>
          <div v-if="vault.error" class="err"><span class="k">错误</span>{{ vault.error }}</div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.page-head .el-button { flex-shrink: 0; }
.hint { margin: 0; color: var(--muted); font-size: 0.85rem; }
.muted { color: var(--muted); }
.mb { margin-bottom: 14px; }

.cards {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  align-items: start;
}
.mon-card:first-child { grid-column: 1 / -1; }
.ch { display: flex; align-items: center; gap: 10px; }
.ct { font-weight: 600; }

.block { margin-bottom: 16px; }
.block-t {
  display: flex;
  justify-content: space-between;
  font-size: 0.88rem;
  margin-bottom: 8px;
}
.kv-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  margin-top: 8px;
  font-size: 0.85rem;
  color: var(--muted);
}
.kv-row b { color: var(--el-text-color-primary); }

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
.kv-grid .k {
  display: inline-block;
  min-width: 4.5em;
  color: var(--muted);
}
.kv-grid .err { color: var(--el-color-danger); }

.stat-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin: 4px 0 16px;
}
.stat {
  flex: 1 1 110px;
  min-width: 110px;
  text-align: center;
  padding: 10px 6px;
  background: var(--el-fill-color-lighter, #f5f7fa);
  border-radius: 8px;
}
.stat-v { font-size: 1.15rem; font-weight: 600; }
.stat-l { font-size: 0.75rem; color: var(--muted); margin-top: 2px; }

.trend { margin-top: 14px; }
.trend-t { font-size: 0.82rem; color: var(--muted); margin-bottom: 4px; }

@media (max-width: 768px) {
  .cards { grid-template-columns: 1fr; }
  .page-head { flex-wrap: wrap; gap: 10px; }
  .page-head .el-button { width: 100%; }
  .tiles { justify-content: space-around; gap: 10px; }
  .stat-row { flex-direction: column; }
}
</style>
