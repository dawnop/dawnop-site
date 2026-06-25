// 读者端运行时：把后台保存的「编译产物」还原成 Vue 组件并管理其样式。
// 注意：这里不含编译器（@vue/compiler-sfc），读者端零编译开销。
import * as Vue from 'vue'

// 产物（compiled）是一段以 Vue 为入参、最终 return 组件定义的函数体。
export function buildComponent(compiled) {
  // 单作者可信场景：运行的是你保存时自己编译好的代码，等同于你写文章本身的信任级别。
  // eslint-disable-next-line no-new-func
  const factory = new Function('Vue', compiled)
  return factory(Vue)
}

// 动态组件的 scoped CSS：按 slug 引用计数注入 <style>，多个实例共享一份，全卸载后移除。
const styleRegistry = new Map() // slug -> { el, count }

export function acquireStyle(slug, css) {
  if (!css) return () => {}
  let entry = styleRegistry.get(slug)
  if (!entry) {
    const el = document.createElement('style')
    el.setAttribute('data-viz-style', slug)
    el.textContent = css
    document.head.appendChild(el)
    entry = { el, count: 0 }
    styleRegistry.set(slug, entry)
  }
  entry.count++
  let released = false
  return () => {
    if (released) return
    released = true
    entry.count--
    if (entry.count <= 0) {
      entry.el.remove()
      styleRegistry.delete(slug)
    }
  }
}
