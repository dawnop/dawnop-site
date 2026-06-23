import { createApp } from 'vue'
import { VueFinderPlugin } from 'vuefinder'
import zhCN from 'vuefinder/dist/locales/zhCN.js'
// Element Plus 改为按需引入（见 vite.config.js）：组件样式由 unplugin 注入，
// 这里只保留主题变量覆盖。命令式组件（ElMessage/ElMessageBox）由 AutoImport 处理。
import './assets/element-theme.css'
import './style.css'
import './assets/admin.css'
import 'vuefinder/dist/vuefinder.css'
import App from './App.vue'
import router from './router'

// md-editor-v3 + KaTeX + 完整 hljs 体积很大，已挪到 setupMdEditor.js，由懒加载的
// 编辑器视图按需导入；前台文章渲染由 MarkdownView（同样懒加载）自带 hljs/katex。
// 首屏入口因此不再背负这些重依赖。

// VueFinder 组件依赖插件 provide 的 VueFinderOptions（含 i18n/locale），必须 use；
// 注册中文语言包并设为默认。Element 语言通过 App.vue 的 el-config-provider 设置。
createApp(App)
  .use(router)
  .use(VueFinderPlugin, { i18n: { zhCN }, locale: 'zhCN' })
  .mount('#app')
