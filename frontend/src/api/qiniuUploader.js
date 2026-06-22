import XHRUpload from '@uppy/xhr-upload'
import client from './client'

// VueFinder 的上传走 Uppy（Driver 没有 upload 方法）。这里用 customUploader
// 让浏览器**直传七牛**，不经后端中转，省服务器流量：
//   1. 上传前向后端要一次性、限定 key 的上传凭证（SecretKey 不出后端）；
//   2. Uppy XHRUpload 把文件 POST 到七牛上传域名（form 字段 token/key/file）；
//   3. 成功后调 /register 登记 path↔key 元信息，列表即可显示。
export function qiniuCustomUploader(uppy, context) {
  uppy.use(XHRUpload, {
    endpoint: 'https://upload.qiniup.com', // 会在预处理里按区域改写
    method: 'POST',
    formData: true,
    fieldName: 'file',
    bundle: false,
    // 把每个文件的 token/key 作为表单字段一起发给七牛
    allowedMetaFields: ['token', 'key'],
  })

  // file.id -> { path, key }，供登记用
  const pending = new Map()

  uppy.addPreProcessor(async (fileIDs) => {
    const dir = context.getTargetPath()
    for (const id of fileIDs) {
      const f = uppy.getFile(id)
      const { data } = await client.post('/fm/upload-token', {
        path: dir,
        name: f.name,
      })
      uppy.setFileMeta(id, { token: data.token, key: data.key })
      uppy.getPlugin('XHRUpload').setOptions({ endpoint: data.up_host })
      pending.set(id, { path: data.path, key: data.key })
    }
  })

  uppy.on('upload-success', async (file) => {
    const m = pending.get(file.id)
    if (!m) return
    pending.delete(file.id)
    await client.post('/fm/register', {
      path: m.path,
      key: m.key,
      size: file.size,
      content_type: file.type || '',
    })
  })
}
