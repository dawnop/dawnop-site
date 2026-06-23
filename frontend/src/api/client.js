import axios from 'axios'
import { ElMessage } from 'element-plus'
import 'element-plus/theme-chalk/el-message.css'
import { auth } from '../store/auth'

// 统一的 axios 实例：自动带 token；401 登出并跳登录页；其余错误统一 toast。
const client = axios.create({ baseURL: '/api' })

client.interceptors.request.use((config) => {
  if (auth.token) {
    config.headers.Authorization = `Bearer ${auth.token}`
  }
  return config
})

client.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const { response, config } = error
    const status = response?.status
    // 登录接口的错误交由登录页自行提示，避免「登录已过期」误报
    const isLogin = config?.url?.includes('/auth/login')

    if (isLogin) {
      return Promise.reject(error)
    }

    if (status === 401) {
      auth.logout()
      ElMessage.warning('登录已过期，请重新登录')
      // 动态导入 router 规避循环依赖（router → views → api → client）
      import('../router').then(({ default: router }) => {
        const cur = router.currentRoute.value
        if (cur.name !== 'admin-login') {
          router.replace({ name: 'admin-login', query: { redirect: cur.fullPath } })
        }
      })
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
