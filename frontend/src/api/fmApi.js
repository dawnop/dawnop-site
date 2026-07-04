// 自建文件管理器的后端对接层（复用后端现成的 /api/fm 那套接口）。
// 后端路径用 `qiniu://<rel>` 前缀；本层对外一律用「相对路径 rel」（根为空串），
// 只在跟后端通信时转成 qiniu:// 形式，组件里不必关心存储前缀。
import * as qiniuJs from 'qiniu-js'
import client from './client'
import { auth } from '../store/auth'

const STORAGE = 'qiniu'

export const toFull = (rel) => `${STORAGE}://${String(rel || '').replace(/^\/+/, '')}`
export const relOf = (full) => String(full || '').replace(/^\w+:\/\//, '').replace(/^\/+/, '')

// 后端 DirEntry → 组件内部行模型（贴近 FileObject）
function mapEntry(e) {
  return {
    name: e.basename,
    path: relOf(e.path),
    is_dir: e.type === 'dir',
    size: e.file_size || 0,
    content_type: e.mime_type || '',
    updated_at: e.last_modified ? e.last_modified * 1000 : null, // ms
  }
}

// 列目录（rel 为空串 = 根）；文件夹在前、各自按名称排序
export async function listDir(rel) {
  const { data } = await client.get('/fm', { params: { path: toFull(rel) } })
  return (data.files || [])
    .map(mapEntry)
    .sort((a, b) => (b.is_dir - a.is_dir) || a.name.localeCompare(b.name, 'zh'))
}

// 浏览器直接发起的 GET（<img>/下载），带不上 Authorization，把 token 放查询串
function withToken(url) {
  const t = auth.token
  return t ? `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(t)}` : url
}
// opts: { w, h, mode }（缩略图；光栅图才生效，svg 等原样）。不传则取原图。
export function previewUrl(rel, opts) {
  let url = `/api/fm/preview?path=${encodeURIComponent(toFull(rel))}`
  if (opts?.w) url += `&w=${opts.w}`
  if (opts?.h) url += `&h=${opts.h}`
  if (opts?.mode) url += `&mode=${opts.mode}`
  return withToken(url)
}
export function downloadUrl(rel) {
  return withToken(`/api/fm/download?path=${encodeURIComponent(toFull(rel))}`)
}

// 文本预览：先要签名 URL，再直连七牛取字节（避免 302 跨域把 Origin 置 null）。
// 直连依赖存储域名 CORS（生产已配；本地开发走七牛测试域名没有），被拦时走后端代理兜底。
// 直连限时 8s：有些网络环境下失败表现为挂起而非快速报错，不限时就轮不到兜底。
export async function textContent(rel) {
  try {
    const { data } = await client.get('/fm/sign', { params: { path: toFull(rel) } })
    const resp = await fetch(data.url, { signal: AbortSignal.timeout(8000) })
    if (!resp.ok) throw new Error(`预览失败: ${resp.status}`)
    return resp.text()
  } catch {
    const { data } = await client.get('/fm/content', {
      params: { path: toFull(rel) },
      responseType: 'text',
      transformResponse: [(d) => d],
    })
    return data
  }
}

// 保存文本（在线编辑）：后端代理写回七牛并更新元数据
export const saveText = (rel, content) =>
  client.post('/fm/save', { path: toFull(rel), content })

// 带进度下载：首选签名 URL 直连七牛流式读字节（不占后端流量）；
// 直连被 CORS 拦（本地开发测试域名）时改走后端代理，进度不丢。
// 拿到 Blob 后由调用方触发另存。sizeHint 兜底 Content-Length 缺失的场景。
export async function downloadBlob(rel, onProgress, sizeHint) {
  try {
    const { data } = await client.get('/fm/sign', { params: { path: toFull(rel) } })
    // 只对「等响应头」限时 8s（直连失败在有些网络下表现为挂起）；开始收流后不设限，大文件慢慢下
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), 8000)
    const resp = await fetch(data.url, { signal: ctrl.signal })
    clearTimeout(timer)
    if (!resp.ok) throw new Error(`下载失败: ${resp.status}`)
    const total = Number(resp.headers.get('content-length')) || sizeHint || 0
    const reader = resp.body.getReader()
    const chunks = []
    let loaded = 0
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      chunks.push(value)
      loaded += value.length
      if (total && onProgress) onProgress(Math.min(1, loaded / total))
    }
    return new Blob(chunks)
  } catch {
    const { data } = await client.get('/fm/content', {
      params: { path: toFull(rel) },
      responseType: 'blob',
      onDownloadProgress: (e) => {
        const total = e.total || sizeHint || 0
        if (total && onProgress) onProgress(Math.min(1, e.loaded / total))
      },
    })
    return data
  }
}

// 增删改（后端都返回当前目录的新 FsData，这里不用它，调用方自行刷新）
export const createFolder = (rel, name) =>
  client.post('/fm/create-folder', { path: toFull(rel), name })
export const rename = (rel, itemRel, name) =>
  client.post('/fm/rename', { path: toFull(rel), item: toFull(itemRel), name })
export const remove = (rel, itemRels) =>
  client.post('/fm/delete', { path: toFull(rel), items: itemRels.map((r) => ({ path: toFull(r) })) })
export const move = (rel, destRel, srcRels) =>
  client.post('/fm/move', { path: toFull(rel), destination: toFull(destRel), sources: srcRels.map(toFull) })
export const copy = (rel, destRel, srcRels) =>
  client.post('/fm/copy', { path: toFull(rel), destination: toFull(destRel), sources: srcRels.map(toFull) })

// 上传 = 前端直传七牛：要凭证 → qiniu-js 直传 → 登记。
// qiniu-js 自动按文件大小选通道：≤4MB 表单直传，>4MB 分片上传（v2，4MB/片、
// 分片并发、localStorage 断点续传），大文件不再受表单上传 1GB 上限约束。
// 文件夹上传时把相对子路径塞进 name（后端 _ensure_dirs 会重建目录）。
// nameOverride：拖拽文件夹上传时 File 没有 webkitRelativePath，由调用方遍历目录树后显式传入。
export async function uploadFile(rel, file, onProgress, nameOverride) {
  const name = nameOverride || file.webkitRelativePath || file.name
  const { data: tk } = await client.post('/fm/upload-token', { path: toFull(rel), name })
  await new Promise((resolve, reject) => {
    const observable = qiniuJs.upload(
      file, tk.key, tk.token,
      { fname: name.split('/').pop() },
      { useCdnDomain: true },
    )
    observable.subscribe({
      next: (res) => {
        if (onProgress && res?.total) onProgress(res.total.percent / 100)
      },
      error: (err) => reject(new Error(err?.message || '上传失败')),
      complete: () => resolve(),
    })
  })
  await client.post('/fm/register', {
    path: tk.path,
    key: tk.key,
    size: file.size,
    content_type: file.type || '',
  })
}

// 存储用量（侧栏用量条）：{used, used_local, used_remote, files, dirs, quota}
export async function stats() {
  const { data } = await client.get('/fm/stats')
  return data
}
