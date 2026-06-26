import client from './client'

// ---- 鉴权 ----
export const authApi = {
  // 后端用 OAuth2 表单，需 application/x-www-form-urlencoded
  login(username, password) {
    const form = new URLSearchParams()
    form.append('username', username)
    form.append('password', password)
    return client.post('/auth/login', form)
  },
  me() {
    return client.get('/auth/me')
  },
}

// ---- 文章 ----
export const articlesApi = {
  listPublished(page = 1, size = 10) {
    return client.get('/articles', { params: { page, size } })
  },
  getBySlug(slug) {
    return client.get(`/articles/${slug}`)
  },
  listAll(page = 1, size = 20, filters = {}) {
    const params = { page, size }
    if (filters.published !== undefined && filters.published !== null)
      params.published = filters.published
    if (filters.page_id !== undefined && filters.page_id !== null)
      params.page_id = filters.page_id
    if (filters.q) params.q = filters.q
    return client.get('/articles/admin', { params })
  },
  getForEdit(id) {
    return client.get(`/articles/admin/${id}`)
  },
  create(data) {
    return client.post('/articles', data)
  },
  update(id, data) {
    return client.put(`/articles/${id}`, data)
  },
  remove(id) {
    return client.delete(`/articles/${id}`)
  },
  // 导出接口需鉴权，故用带 token 的请求取 blob，再触发下载
  exportMarkdown(id) {
    return client.get(`/articles/${id}/export`, { responseType: 'blob' })
  },
}

// 文件管理改用 VueFinder + 其内置 RemoteDriver（见 api/vuefinderDriver.js），
// 直接对接 /api/fm，不再走这里的 axios 封装。

// ---- 页面 ----
export const pagesApi = {
  nav() {
    return client.get('/pages/nav')
  },
  get(slug) {
    return client.get(`/pages/${slug}`)
  },
  listArticles(slug, page = 1, size = 10) {
    return client.get(`/pages/${slug}/articles`, { params: { page, size } })
  },
  listAll() {
    return client.get('/pages/admin')
  },
  create(data) {
    return client.post('/pages', data)
  },
  update(id, data) {
    return client.put(`/pages/${id}`, data)
  },
  remove(id) {
    return client.delete(`/pages/${id}`)
  },
  reorder(ids) {
    return client.post('/pages/reorder', { ids })
  },
}

// ---- 动态可视化组件（后台可编辑的交互 viz）----
export const vizApi = {
  listAll() {
    return client.get('/viz')
  },
  get(slug) {
    return client.get(`/viz/${slug}`)
  },
  create(data) {
    return client.post('/viz', data)
  },
  update(id, data) {
    return client.put(`/viz/${id}`, data)
  },
  remove(id) {
    return client.delete(`/viz/${id}`)
  },
}
