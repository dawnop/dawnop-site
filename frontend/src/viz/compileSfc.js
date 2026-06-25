// 仅后台用：浏览器内把 Vue SFC 源码编译成「读者端可 eval 的产物」。
// 编译器（@vue/compiler-sfc）较重，按需动态 import —— 只在编辑/预览可视化时才下载，
// 不进首屏、更不进读者端。这与 Vue SFC Playground 的浏览器内编译同源。
//
// 产物约定（与 viz/runtime.js 的 buildComponent 对齐）：
//   compiled = 一段函数体，签名 (Vue) => component，内部 import 'vue' 改写为从 Vue 取。
//   style    = 编译后的 scoped CSS（选择器形如 .x[data-v-<id>]）。
//   组件挂上 __scopeId = 'data-v-<id>'，Vue 运行时会给其所有元素加该属性，使 scoped 生效。

// 用浏览器构建，避免 Node 内置依赖
const loadCompiler = () => import('@vue/compiler-sfc/dist/compiler-sfc.esm-browser.js')

export async function compileSfc(source, slug) {
  const { parse, compileScript, compileStyle } = await loadCompiler()
  const id = (slug && /^[a-z0-9-]+$/.test(slug) ? slug : 'preview')
  const scopeId = `data-v-${id}`
  const filename = `${id}.vue`

  const { descriptor, errors } = parse(source, { filename })
  if (errors && errors.length) throw new Error(errors[0].message || String(errors[0]))
  if (!descriptor.scriptSetup && !descriptor.script) {
    throw new Error('组件至少要有一个 <script setup>')
  }

  const script = compileScript(descriptor, { id, inlineTemplate: true })
  const compiled = esmToFactoryBody(script.content, scopeId)

  let style = ''
  for (const s of descriptor.styles) {
    const res = compileStyle({ source: s.content, id, scoped: s.scoped, filename })
    if (res.errors && res.errors.length) throw new Error(res.errors[0].message || String(res.errors[0]))
    style += res.code + '\n'
  }

  return { compiled, style }
}

// 把 compileScript 产出的 ESM 改写成 (Vue) => component 的函数体：
//  - 所有 import 必须来自 'vue'（否则抛错，挡掉任意外部模块），合并为从注入的 Vue 解构；
//  - export default X → const __viz__ = X，末尾设 __scopeId 并 return。
function esmToFactoryBody(code, scopeId) {
  const importRe = /import\s+(?:([\w$]+)\s*,?\s*)?(?:\{([^}]*)\})?\s*from\s*(['"])([^'"]+)\3;?/g
  const names = []
  code = code.replace(importRe, (_m, _def, named, _q, src) => {
    if (src !== 'vue') {
      throw new Error(`组件只能 import 'vue'，不支持：${src}`)
    }
    if (named) {
      for (let part of named.split(',')) {
        part = part.trim()
        if (!part) continue
        const as = part.split(/\s+as\s+/)
        names.push(as.length === 2 ? `${as[0].trim()}: ${as[1].trim()}` : part)
      }
    }
    return ''
  })

  if (!/export\s+default\s+/.test(code)) {
    throw new Error('组件缺少默认导出')
  }
  code = code.replace(/export\s+default\s+/, 'const __viz__ = ')

  const head = names.length ? `const { ${names.join(', ')} } = Vue;\n` : ''
  const tail = `\n__viz__.__scopeId = ${JSON.stringify(scopeId)};\nreturn __viz__;`
  return head + code + tail
}
