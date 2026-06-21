import { reactive } from 'vue'

// 轻量登录态：token 持久化到 localStorage，避免引入 pinia。
const TOKEN_KEY = 'dawnop_token'

export const auth = reactive({
  token: localStorage.getItem(TOKEN_KEY) || '',
  user: null,

  get isAuthenticated() {
    return !!this.token
  },

  setToken(token) {
    this.token = token
    localStorage.setItem(TOKEN_KEY, token)
  },

  logout() {
    this.token = ''
    this.user = null
    localStorage.removeItem(TOKEN_KEY)
  },
})
