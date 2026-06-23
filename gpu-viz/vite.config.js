import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 部署在 dawnop.com/gpu/ 子路径下：base 必须为 '/gpu/'，否则打包后的资源会以 / 开头、
// 在子路径下 404。本地 dev 也会在 http://localhost:5173/gpu/ 伺服。
export default defineConfig({
  base: '/gpu/',
  plugins: [vue()],
})
