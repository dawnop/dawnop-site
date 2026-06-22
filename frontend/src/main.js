import { createApp } from 'vue'
import { VueFinderPlugin } from 'vuefinder'
import zhCN from 'vuefinder/dist/locales/zhCN.js'
import { config as mdConfig } from 'md-editor-v3'
import katexLib from 'katex'
import './style.css'
import './assets/admin.css'
import 'vuefinder/dist/vuefinder.css'
import 'md-editor-v3/lib/style.css'
import 'katex/dist/katex.min.css'
import App from './App.vue'
import router from './router'

// md-editor-v3 默认从 CDN 拉 KaTeX；改用本地实例，离线/无 CDN 也能渲染公式，
// 且与文章页(MarkdownView 同样用 KaTeX)保持一致。
mdConfig({
  editorExtensions: {
    katex: { instance: katexLib },
  },
})

// VueFinder 组件依赖插件 provide 的 VueFinderOptions（含 i18n/locale），必须 use；
// 注册中文语言包并设为默认。
createApp(App)
  .use(router)
  .use(VueFinderPlugin, { i18n: { zhCN }, locale: 'zhCN' })
  .mount('#app')
