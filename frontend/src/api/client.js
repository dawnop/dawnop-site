import axios from 'axios'
import { ElMessage } from 'element-plus'
import { auth } from '../store/auth'
import { progressStart, progressDone } from '../progress'

// 统一的 axios 实例：自动带 token；顶部进度条；401 登出并跳登录页；其余错误统一 toast。
const client = axios.create({ baseURL: '/api' })

// 一次过期只提示一次，见下方 401 分支
let expiryHandling = false

client.interceptors.request.use((config) => {
  progressStart()
  if (auth.token) {
    config.headers.Authorization = `Bearer ${auth.token}`
  }
  return config
})

client.interceptors.response.use(
  (resp) => {
    progressDone()
    return resp
  },
  (error) => {
    progressDone()
    const { response, config } = error
    const status = response?.status
    // 登录接口的错误交由登录页自行提示，避免「登录已过期」误报
    const isLogin = config?.url?.includes('/auth/login')

    if (isLogin) {
      return Promise.reject(error)
    }

    if (status === 401) {
      // 页面常并发多个请求（如 /admin/files 的 loadCwd + loadStats + loadConf），会一起 401。
      // 下面按路由名去重不够：它在动态 import 的回调里跑，跳转落地前所有人都能通过。
      // 故在同步阶段就抢占，只让第一个提示与跳转，其余静默 reject。
      if (!expiryHandling) {
        expiryHandling = true
        auth.logout()
        ElMessage.warning('登录已过期，请重新登录')
        // 动态导入 router 规避循环依赖（router → views → api → client）
        import('../router')
          .then(({ default: router }) => {
            const cur = router.currentRoute.value
            if (cur.name !== 'admin-login') {
              return router.replace({ name: 'admin-login', query: { redirect: cur.fullPath } })
            }
          })
          // 跳转落地即复位：重新登录后再次过期仍要提示，不能一直锁着
          .finally(() => {
            expiryHandling = false
          })
      }
    } else if (status) {
      const detail = response?.data?.detail
      ElMessage.error(typeof detail === 'string' ? detail : '请求失败，请稍后重试')
    } else {
      ElMessage.error('网络异常，请检查连接')
    }
    return Promise.reject(error)
  },
)

export default client
