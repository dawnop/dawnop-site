// 公开上传链接页（/drop）的后端对接层。持链接的人是匿名访客，故这里刻意与管理端隔离：
//  - 独立 axios 实例，不 import client.js：不带管理员 Authorization、不挂「401 跳登录」与全局 toast 拦截器；
//  - drop token 只放请求头 X-Drop-Token，绝不进 query（会进 nginx 访问日志与 Referer）。
// 约定同 fmApi：一律返回解包后的响应体。错误原样抛出 axios error，由页面读 status / detail。
import axios from 'axios'
import { directUpload } from './qiniuUpload'

const http = axios.create({ baseURL: '/api' })

const hdr = (token) => ({ headers: { 'X-Drop-Token': token } })

// {label, dir_name, expires_at, max_files, max_file_bytes, max_total_bytes, used_files, used_bytes}
export async function info(token) {
  return (await http.get('/drop', hdr(token))).data
}

// 单个文件：要上传凭证（服务端预检额度并定最终文件名）→ qiniu-js 直传 → 登记。
// register 只交 key：落到哪个路径由服务端按签发时的账本决定。返回 {name, size}。
// onStage(stage) 可选，stage ∈ 'token' | 'upload' | 'register'，供页面显示当前阶段。
export async function uploadFile(token, file, onProgress, onStage) {
  onStage?.('token')
  const { data: tk } = await http.post(
    '/drop/upload-token',
    { name: file.name, size: file.size },
    hdr(token),
  )
  onStage?.('upload')
  await directUpload(file, tk.key, tk.token, tk.name || file.name, onProgress)
  onStage?.('register')
  return (await http.post('/drop/register', { key: tk.key }, hdr(token))).data
}

// 把错误转成给访客看的一句话：后端 detail 原样显示，没有 detail 时按状态码兜底。
export function errorText(err) {
  const status = err?.response?.status
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (status === 404) return '上传链接无效'
  if (status === 410) return '上传链接已失效'
  if (status === 413) return '超出上传额度'
  if (status) return `请求失败（${status}）`
  return err?.message || '网络异常，请检查连接'
}
