// 把 @vue/compiler-sfc 的 compileScript 产物（ESM）改写成「(Vue) => component」的函数体。
// 前台运行时用 new Function('Vue', body) 还原组件（见 viz/runtime.js）。
// 浏览器内编译（compileSfc.js）与 seed 生成器（scripts/gen-viz-seed.mjs，Node）共用此函数，避免漂移。
//
// 规则：所有 import 必须来自 'vue'（否则抛错，挡掉任意外部模块），合并为从注入的 Vue 解构；
// export default X → const __viz__ = X，末尾设 __scopeId 并 return。
export function esmToFactoryBody(code, scopeId) {
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
