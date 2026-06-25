import { defineConfig } from 'vite'
import fs from 'node:fs'
import path from 'node:path'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import viteCompression from 'vite-plugin-compression'

// Element Plus 按需引入每个组件的样式（importStyle: 'css'，生产只打包用到的样式，首屏精简）。
// 但开发期这些样式是「首次用到某组件时」才被 Vite 发现 → 触发整页重载 → 打断当前路由跳转
// （表现：第一次点没跳、第二次才跳）。故把全部组件样式路径喂给 optimizeDeps.include，
// 让 Vite 启动时一次性预构建，开发期就不会再中途发现新依赖。仅影响开发，生产仍按需 tree-shake。
function elementPlusStyleDeps() {
  const base = 'node_modules/element-plus/es/components'
  try {
    return fs
      .readdirSync(base)
      .filter((name) => fs.existsSync(path.join(base, name, 'style/css.mjs')))
      .map((name) => `element-plus/es/components/${name}/style/css`)
  } catch {
    return []
  }
}

// 产物 gzip -9 预压缩（服务器 gzip_static 直接发）；vue/router/axios 拆出稳定 vendor chunk。
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
  optimizeDeps: {
    // 预扫描所有源码 + 显式预构建 Element Plus 全部组件样式，避免开发期首次进入某页时
    // 「发现新依赖→整页重载」打断路由跳转。仅影响开发模式。
    entries: ['index.html', 'src/**/*.vue', 'src/**/*.js'],
    include: elementPlusStyleDeps(),
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
