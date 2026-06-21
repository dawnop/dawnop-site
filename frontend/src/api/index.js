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
  listAll(page = 1, size = 20) {
    return client.get('/articles/admin', { params: { page, size } })
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
  importMarkdown(file, published = false) {
    const form = new FormData()
    form.append('file', file)
    form.append('published', published)
    return client.post('/articles/import', form)
  },
  // 导出接口需鉴权，故用带 token 的请求取 blob，再触发下载
  exportMarkdown(id) {
    return client.get(`/articles/${id}/export`, { responseType: 'blob' })
  },
}

// ---- 文件 ----
export const filesApi = {
  list(page = 1, size = 20) {
    return client.get('/files', { params: { page, size } })
  },
  // relativePath 用于文件夹导入：作为上传文件名携带相对路径
  upload(file, relativePath) {
    const form = new FormData()
    form.append('file', file, relativePath || file.name)
    return client.post('/files/upload', form)
  },
  url(id) {
    return client.get(`/files/${id}/url`)
  },
  remove(id) {
    return client.delete(`/files/${id}`)
  },
}

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
