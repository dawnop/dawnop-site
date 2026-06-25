// 可视化解析：先查「内置组件」（随博客构建打包、懒加载），再查「后端动态组件」。
// 二者共存：重型/常用的可放内置；随手写的交互在后台编辑、存库、动态加载。
import { buildComponent } from './runtime'

const builtin = {
  coalescing: () => import('./MatmulAccessSVG.vue').then((m) => m.default),
}

export function builtinIds() {
  return Object.keys(builtin)
}

// 动态组件按 id 缓存（编辑器预览每次重渲染会重新挂载、一页可能多处引用，避免重复 fetch/编译）。
const dynCache = new Map()

// 清掉某个动态组件的缓存（后台保存后调用，使下次渲染拿到新版本）。
export function invalidateViz(id) {
  dynCache.delete(id)
}

// 解析一个 viz id → { component, style }。
// style 仅动态组件需要（内置组件的 scoped 样式已随 SFC 打包，返回 null）。
export async function resolveViz(id) {
  if (builtin[id]) {
    return { component: await builtin[id](), style: null }
  }
  if (dynCache.has(id)) return dynCache.get(id)
  // 公开读取，用裸 fetch 避开 axios 全局错误 toast（拼错 id 不该弹全局错误）
  const res = await fetch(`/api/viz/${encodeURIComponent(id)}`)
  if (!res.ok) throw new Error(`viz not found: ${id}`)
  const data = await res.json()
  if (!data.compiled) throw new Error(`viz not compiled: ${id}`)
  const built = { component: buildComponent(data.compiled), style: data.style }
  dynCache.set(id, built)
  return built
}
