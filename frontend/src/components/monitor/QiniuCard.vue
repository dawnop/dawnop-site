<script setup>
// 七牛 Kodo：存储用量条 / CDN 流量包条 / 统计三宫格 / 30 天趋势图
import { computed } from 'vue'
import MiniChart from '../MiniChart.vue'
import { fmtBytes } from '../../utils/format'
import { pct, barColor, dateYmd, shortDate, labelFromUnix, fmtMbps } from './helpers'

const props = defineProps({
  qn: { type: Object, default: null },
})

const spaceTrend = computed(() =>
  (props.qn?.space_trend || []).map((p) => ({ label: labelFromUnix(p.t), value: p.v })),
)
const flowTrend = computed(() =>
  (props.qn?.flow_trend || []).map((p) => ({ label: shortDate(p.t), value: p.v })),
)
const cdnTrend = computed(() =>
  (props.qn?.cdn_trend || []).map((p) => ({ label: labelFromUnix(p.t), value: p.v })),
)
// 存储条分母：优先用存储资源包容量（API），无包则回落全局设置配额
const storageTotal = computed(() => props.qn?.storage_pack?.capacity || props.qn?.quota || 0)
const storageRemain = computed(() => Math.max(0, storageTotal.value - (props.qn?.space || 0)))
</script>

<template>
  <el-card shadow="never" class="mon-card">
    <template #header>
      <div class="ch">
        <span class="ct">七牛 Kodo · 对象存储</span>
        <span v-if="qn?.bucket" class="muted">{{ qn.bucket }}</span>
      </div>
    </template>

    <el-alert
      v-if="qn && !qn.configured"
      type="info"
      :closable="false"
      show-icon
      title="未配置七牛密钥"
    />
    <el-alert
      v-else-if="qn?.error"
      type="warning"
      :closable="false"
      show-icon
      :title="'七牛统计获取失败：' + qn.error"
    />

    <template v-else-if="qn">
      <div class="block">
        <div class="block-t">
          <span>存储用量</span>
          <span class="muted">
            <template v-if="qn.storage_pack">
              存储包 {{ fmtBytes(qn.storage_pack.capacity) }}
              <template v-if="qn.storage_pack.expire">
                · 有效期至 {{ dateYmd(qn.storage_pack.expire) }}</template
              >
            </template>
            <template v-else>配额 {{ fmtBytes(qn.quota) }}</template>
          </span>
        </div>
        <el-progress
          :percentage="pct(qn.space, storageTotal)"
          :color="barColor(pct(qn.space, storageTotal))"
          :stroke-width="10"
        />
        <div class="kv-row">
          <span
            >已用 <b>{{ fmtBytes(qn.space) }}</b
            ><template v-if="qn.storage_pack"> · 剩余 {{ fmtBytes(storageRemain) }}</template></span
          >
        </div>
      </div>

      <div v-if="qn.cdn_pack" class="block">
        <div class="block-t">
          <span>CDN 流量包</span>
          <span class="muted">
            总 {{ fmtBytes(qn.cdn_pack.total) }}
            <template v-if="qn.cdn_pack.expire">
              · 有效期至 {{ dateYmd(qn.cdn_pack.expire) }}</template
            >
          </span>
        </div>
        <el-progress
          :percentage="pct(qn.cdn_pack.used, qn.cdn_pack.total)"
          :color="barColor(pct(qn.cdn_pack.used, qn.cdn_pack.total))"
          :stroke-width="10"
        />
        <div class="kv-row">
          <span
            >已用 <b>{{ fmtBytes(qn.cdn_pack.used) }}</b> · 剩余
            {{ fmtBytes(qn.cdn_pack.remain) }}</span
          >
          <span v-if="qn.cdn_peak_bps">峰值带宽 {{ fmtMbps(qn.cdn_peak_bps) }}</span>
          <span v-if="qn.cdn_domains?.length" class="muted">{{ qn.cdn_domains.join('、') }}</span>
        </div>
      </div>
      <el-alert
        v-if="qn.cdn_error"
        type="warning"
        :closable="false"
        show-icon
        :title="'CDN 用量获取失败：' + qn.cdn_error"
        class="mb"
      />

      <div class="stat-row">
        <div class="stat">
          <div class="stat-v">{{ qn.count }}</div>
          <div class="stat-l">文件对象</div>
        </div>
        <div class="stat">
          <div class="stat-v">{{ fmtBytes(qn.flow_30d) }}</div>
          <div class="stat-l">源站流出 · 30 天</div>
        </div>
        <div class="stat">
          <div class="stat-v">{{ qn.hits_30d }}</div>
          <div class="stat-l">请求次数 · 30 天</div>
        </div>
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
</template>

<style scoped>
.mon-card {
  break-inside: avoid;
  margin-bottom: 16px;
}
.mb {
  margin-bottom: 14px;
}
.muted {
  color: var(--muted);
}
.ch {
  display: flex;
  align-items: center;
  gap: 10px;
}
.ct {
  font-weight: 600;
}

.block {
  margin-bottom: 16px;
}
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
.kv-row b {
  color: var(--el-text-color-primary);
}

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
.stat-v {
  font-size: 1.15rem;
  font-weight: 600;
}
.stat-l {
  font-size: 0.75rem;
  color: var(--muted);
  margin-top: 2px;
}

.trend {
  margin-top: 14px;
}
.trend-t {
  font-size: 0.82rem;
  color: var(--muted);
  margin-bottom: 4px;
}

@media (max-width: 768px) {
  .stat-row {
    flex-direction: column;
  }
}
</style>
