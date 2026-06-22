import { createApp } from 'vue'
import { VueFinderPlugin } from 'vuefinder'
import zhCN from 'vuefinder/dist/locales/zhCN.js'
import './style.css'
import 'vuefinder/dist/vuefinder.css'
import App from './App.vue'
import router from './router'

// VueFinder 组件依赖插件 provide 的 VueFinderOptions（含 i18n/locale），必须 use；
// 注册中文语言包并设为默认。
createApp(App)
  .use(router)
  .use(VueFinderPlugin, { i18n: { zhCN }, locale: 'zhCN' })
  .mount('#app')
