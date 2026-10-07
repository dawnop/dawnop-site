// 后台共用配置，按登录会话缓存；保存后同页立即应用。
import { reactive, watch } from 'vue'
import { settingsApi } from '../api'
import { auth } from './auth'

const defaults = {
  upload_concurrency: 3,
  download_concurrency: 3,
  storage_quota_gb: 10,
  text_preview_max_kb: 512,
  admin_page_size: 20,
  admin_compact: 0,
  drop_expiry_hours: 24,
  drop_max_files: 20,
  drop_file_max_mb: 100,
  drop_total_max_mb: 1024,
}
export const adminSettings = reactive({ values: { ...defaults }, ready: false })
let pending = null
watch(
  () => auth.token,
  () => {
    Object.assign(adminSettings.values, defaults)
    adminSettings.ready = false
    pending = null
  },
  { flush: 'sync' },
)
export function applyAdminSettings(values) {
  Object.assign(adminSettings.values, values)
  adminSettings.ready = true
}
export function loadAdminSettings(force = false) {
  if (pending) return pending
  if (adminSettings.ready && !force) return Promise.resolve(adminSettings.values)
  const token = auth.token
  const request = settingsApi.get().then(({ data }) => {
    if (auth.token !== token) throw new Error('登录已变更')
    applyAdminSettings(data)
    return data
  })
  pending = request
  request
    .finally(() => {
      if (pending === request) pending = null
    })
    .catch(() => {})
  return request
}
