// 七牛直传的共用实现：管理端文件管理器（fmApi.uploadFile）与公开上传链接页（dropApi）都走这里。
// 本模块不依赖任何鉴权实例，只拿后端签好的「key + 上传凭证」干活，故公开页引入它不会带进管理员 token。
//
// qiniu-js 按文件大小自动选通道：≤4MB 表单直传，>4MB 分片上传（v2，4MB/片、分片并发、
// localStorage 断点续传），大文件不再受表单上传 1GB 上限约束。上传域名由 qiniu-js 按凭证里的
// 空间自动查询区域（useCdnDomain 走加速域名），不用后端回的 up_host：那是后端代理上传自己用的
// 地址，管理端一直不读它，这里保持同样的行为。
import * as qiniuJs from 'qiniu-js'

// file: File/Blob；key、token：后端 /upload-token 回的对象 key 与上传凭证；
// fname：七牛侧记录的原始文件名；onProgress(0..1) 可选。成功 resolve，失败 reject Error。
export function directUpload(file, key, token, fname, onProgress) {
  return new Promise((resolve, reject) => {
    const observable = qiniuJs.upload(file, key, token, { fname }, { useCdnDomain: true })
    observable.subscribe({
      next: (res) => {
        if (onProgress && res?.total) onProgress(res.total.percent / 100)
      },
      error: (err) => reject(new Error(err?.message || '上传失败')),
      complete: () => resolve(),
    })
  })
}
