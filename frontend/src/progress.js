// 顶部加载进度条（NProgress）。路由切换（含懒加载 chunk 拉取）与 API 请求都接到这里，
// 用一个引用计数共享同一条进度条：只有当所有进行中的任务都结束时才收尾，避免闪烁/提前结束。
import NProgress from 'nprogress'
import 'nprogress/nprogress.css'

NProgress.configure({ showSpinner: false, trickleSpeed: 120, minimum: 0.1 })

let pending = 0

export function progressStart() {
  if (pending === 0) NProgress.start()
  pending += 1
}

export function progressDone() {
  pending = Math.max(0, pending - 1)
  if (pending === 0) NProgress.done()
}
