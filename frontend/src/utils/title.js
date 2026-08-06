// 页面标题：站名后缀原本在路由守卫与四个动态页里各拼一次。
export const BASE_TITLE = 'dawnop'

// 传入具体标题拼成「X · dawnop」；传空/不传则只留站名。
export function setTitle(t) {
  document.title = t ? `${t} · ${BASE_TITLE}` : BASE_TITLE
}
