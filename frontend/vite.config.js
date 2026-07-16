import { defineConfig, loadEnv } from 'vite'
import fs from 'node:fs'
import path from 'node:path'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import ElementPlus from 'unplugin-element-plus/vite'
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
export default defineConfig(({ command, mode }) => {
  // 静态资源 CDN：VITE_CDN_BASE 在 .env.local 配置（不入库，同备案号的做法）。
  // 仅构建时生效——资源 URL 指向 CDN 加速域名回源缓存；index.html 与 /api 仍走源站。
  // dev 保持 '/'；未配置则构建也走 '/'（fork 者零配置可用）。
  const env = loadEnv(mode, process.cwd(), '')
  const base = command === 'build' && env.VITE_CDN_BASE ? env.VITE_CDN_BASE : '/'
  return {
  base,
  plugins: [
    vue(),
    AutoImport({ resolvers: [ElementPlusResolver()] }),
    Components({ resolvers: [ElementPlusResolver()] }),
    // 上面两个 unplugin 只覆盖「自动引入」的用法：AutoImport 管裸用的 ElMessage/ElMessageBox，
    // Components 管模板里的 <el-*>，两者在注入符号时顺带注入其样式。但凡代码里写了显式
    // import { ElMessageBox } from 'element-plus'，AutoImport 就不再接管该符号，样式也就没人注入
    // ——弹窗会退化成无样式裸框（曾导致文件管理器/编辑页的确认框跑到左上角、背景透明）。
    // 本插件把显式 import 也改写成带样式 side-effect 的深引入，堵住这个口子。
    ElementPlus({ useSource: false }),
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
  }
})
