import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

// Element Plus 按需引入：只把用到的组件/指令及其样式打进包里，避免全量引入
// 把整个库塞进首屏入口 chunk（首页本来只用到很少几个组件）。
// AutoImport 解析 ElMessage/ElMessageBox 等命令式 API 并自动带上其样式。
// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    AutoImport({ resolvers: [ElementPlusResolver()] }),
    Components({ resolvers: [ElementPlusResolver()] }),
  ],
  server: {
    proxy: {
      // 开发期把 /api 反代到本地 FastAPI，避免 CORS 与端口问题
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
