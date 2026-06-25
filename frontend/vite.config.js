import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import viteCompression from 'vite-plugin-compression'

// Element Plus 按需引入（见下）；产物 gzip -9 预压缩（服务器 gzip_static 直接发，
// 比运行时 gzip-6 更小且省 CPU）；vue/router/axios 拆出稳定 vendor chunk 利于回访缓存。
// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    AutoImport({ resolvers: [ElementPlusResolver()] }),
    Components({ resolvers: [ElementPlusResolver()] }),
    // 构建期生成 .gz（gzip -9）。服务器 nginx 开 gzip_static 直接发预压缩文件。
    viteCompression({ algorithm: 'gzip', ext: '.gz', threshold: 1024, deleteOriginFile: false }),
  ],
  build: {
    rollupOptions: {
      output: {
        // 只把极少变动的框架拆成 vendor；Element Plus 保持按需自动分包（勿强行合并，
        // 否则会把后台专用组件也拽进首屏 eager chunk）。
        manualChunks(id) {
          // @vue/compiler-sfc（后台浏览器内编译 viz 用）须留在自己的懒加载 chunk，
          // 绝不能并进首屏 eager 的 vendor，否则把数百 KB 编译器推给所有读者。
          if (id.includes('compiler-sfc')) return
          if (/[\\/]node_modules[\\/](vue|vue-router|@vue|axios)[\\/]/.test(id)) {
            return 'vendor'
          }
        },
      },
    },
  },
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
