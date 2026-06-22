import { createApp } from 'vue'
import { VueFinderPlugin } from 'vuefinder'
import zhCN from 'vuefinder/dist/locales/zhCN.js'
import { config as mdConfig } from 'md-editor-v3'
import katexLib from 'katex'
import hljs from 'highlight.js'
import './style.css'
import './assets/admin.css'
import 'vuefinder/dist/vuefinder.css'
import 'md-editor-v3/lib/style.css'
import 'highlight.js/styles/github.css'
import 'katex/dist/katex.min.css'
import App from './App.vue'
import router from './router'

// md-editor-v3 默认从 CDN 拉 KaTeX 与代码高亮（hljs）：CDN 在本环境被拦截，
// 导致编辑器预览代码无语法着色、且用内置深色底。这里全部本地化：
//  - KaTeX 用本地实例；
//  - 代码高亮用本地 hljs 实例（md-editor 自带 hljs 时不再管主题样式）；
//  - 全局引入 github 浅色主题，提供 .hljs-* 语法着色（md-editor 自身不定义这些 token 颜色）；
//  - 深色代码块底色由 admin.css 覆盖 --md-theme-code-* 变量为浅色。
// 三者合力使后台编辑器预览与前台文章页(MarkdownView 同用 hljs + github.css)完全一致。
mdConfig({
  editorExtensions: {
    katex: { instance: katexLib },
    highlight: { instance: hljs },
  },
})

// VueFinder 组件依赖插件 provide 的 VueFinderOptions（含 i18n/locale），必须 use；
// 注册中文语言包并设为默认。
createApp(App)
  .use(router)
  .use(VueFinderPlugin, { i18n: { zhCN }, locale: 'zhCN' })
  .mount('#app')
