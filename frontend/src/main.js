import { createApp } from 'vue'
// Element Plus 改为按需引入（见 vite.config.js）：组件样式由 unplugin 注入，
// 这里只保留主题变量覆盖。命令式组件（ElMessage/ElMessageBox）裸用走 AutoImport、
// 显式 import 走 unplugin-element-plus，两条路都会带上各自样式。
import './assets/element-theme.css'
import './style.css'
import './assets/admin.css'
import './assets/responsive.css'
import App from './App.vue'
import router from './router'

// 重依赖均不在首屏入口：
//  - md-editor-v3 + KaTeX + 完整 hljs → setupMdEditor.js，由懒加载编辑器视图导入；
//  - 前台文章渲染由 MarkdownView（懒加载）自带 hljs/katex；
//  - 文件管理（含 qiniu-js）→ 懒加载的 FilesLabView 按需引入。

createApp(App).use(router).mount('#app')
