import { RemoteDriver } from 'vuefinder'
import client from './client'

// 基于 VueFinder 内置 RemoteDriver，对接后端 /api/fm。
// 预览/下载是浏览器直接发起的 GET（<img> src、下载链接），无法携带
// Authorization 头，因此把 token 追加到查询串；后端 get_current_user_flexible
// 支持从 ?token= 读取。其余操作仍走 Bearer 头。
export function createQiniuDriver(token) {
  const driver = new RemoteDriver({
    baseURL: '/api/fm',
    token,
  })

  const withToken = (url) => {
    const t = driver.config?.token
    if (!t) return url
    return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(t)
  }

  const origPreview = driver.getPreviewUrl.bind(driver)
  const origDownload = driver.getDownloadUrl.bind(driver)
  driver.getPreviewUrl = (params) => withToken(origPreview(params))
  driver.getDownloadUrl = (params) => withToken(origDownload(params))

  // 文本/代码在线预览（getContent）：若沿用「后端 302 跳七牛」，浏览器跨域重定向时
  // 会把 Origin 置为 null，七牛按 origin 配的 CORS 便不返回 ACAO。改为先向后端要签名 URL，
  // 再**直连七牛**（无重定向，Origin 正常）即可命中 CORS 读到内容。
  // 图片 <img> / 下载仍走 302（不需要 CORS）。
  driver.getContent = async ({ path, signal }) => {
    const { data } = await client.get('/fm/sign', { params: { path }, signal })
    const resp = await fetch(data.url, { signal })
    if (!resp.ok) throw new Error('预览失败: ' + resp.status)
    return {
      content: await resp.text(),
      mimeType: resp.headers.get('Content-Type') || undefined,
    }
  }

  return driver
}
