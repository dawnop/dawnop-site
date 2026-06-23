// md-editor-v3 配置（仅后台编辑器用）。作为副作用模块，被 ArticleEditView /
// PageEditView 这两个**懒加载**视图导入，从而把 md-editor + katex + 完整 hljs 及其
// 样式打进编辑器的懒加载 chunk，而不是首屏入口 chunk（首页/列表页根本用不到它们）。
//
// md-editor-v3 默认从 CDN 拉 KaTeX 与 hljs（本环境被拦截），这里全部本地化：
//  - KaTeX 用本地实例；
//  - 代码高亮用本地 hljs 实例；
//  - github 浅色主题提供 .hljs-* 着色（md-editor 自身不定义这些 token 颜色）；
//  - 深色代码块底色由 admin.css 覆盖 --md-theme-code-* 变量为浅色。
import { config as mdConfig } from 'md-editor-v3'
import katexLib from 'katex'
import hljs from 'highlight.js'
import 'md-editor-v3/lib/style.css'
import 'highlight.js/styles/github.css'
import 'katex/dist/katex.min.css'

mdConfig({
  editorExtensions: {
    katex: { instance: katexLib },
    highlight: { instance: hljs },
  },
})
