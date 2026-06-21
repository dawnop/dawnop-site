import axios from 'axios'
import { auth } from '../store/auth'

// 统一的 axios 实例：自动带 token，401 时登出并跳登录页。
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
    if (error.response && error.response.status === 401) {
      auth.logout()
      if (!location.hash.startsWith('#/admin/login')) {
        location.hash = '#/admin/login'
      }
    }
    return Promise.reject(error)
  },
)

export default client
