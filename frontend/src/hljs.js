// 共享的 highlight.js 实例：用 core + 按需注册常用语言，避免引入全语言包
// （全包数百 KB）。前台 MarkdownView 与后台 md-editor 共用本实例，确保高亮一致、
// 且只打包这些语言。需要新语言时在此追加 registerLanguage 即可。
import hljs from 'highlight.js/lib/core'

import bash from 'highlight.js/lib/languages/bash'
import c from 'highlight.js/lib/languages/c'
import cpp from 'highlight.js/lib/languages/cpp'
import csharp from 'highlight.js/lib/languages/csharp'
import css from 'highlight.js/lib/languages/css'
import go from 'highlight.js/lib/languages/go'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import llvm from 'highlight.js/lib/languages/llvm' // 给 PTX/asm 着色（%寄存器、;注释、类型）
import markdown from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import rust from 'highlight.js/lib/languages/rust'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml' // 含 HTML/Vue 模板
import yaml from 'highlight.js/lib/languages/yaml'

const langs = {
  bash, c, cpp, csharp, css, go, java, javascript, json,
  llvm, markdown, python, rust, sql, typescript, xml, yaml,
}
for (const [name, def] of Object.entries(langs)) {
  hljs.registerLanguage(name, def)
}
// 常见别名
hljs.registerAliases(['js'], { languageName: 'javascript' })
hljs.registerAliases(['ts'], { languageName: 'typescript' })
hljs.registerAliases(['sh', 'shell', 'zsh'], { languageName: 'bash' })
hljs.registerAliases(['html', 'vue'], { languageName: 'xml' })
hljs.registerAliases(['py'], { languageName: 'python' })
hljs.registerAliases(['yml'], { languageName: 'yaml' })
// PTX 没有专门的 hljs 语法，用 LLVM 语法着色最接近（%寄存器、; 注释、.f32 类型）
hljs.registerAliases(['ptx', 'asm'], { languageName: 'llvm' })

export default hljs
