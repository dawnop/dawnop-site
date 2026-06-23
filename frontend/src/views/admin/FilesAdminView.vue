<script setup>
import { getCurrentInstance } from 'vue'
import { VueFinder, VueFinderPlugin } from 'vuefinder'
import zhCN from 'vuefinder/dist/locales/zhCN.js'
import 'vuefinder/dist/vuefinder.css'
import { createQiniuDriver } from '../../api/vuefinderDriver'
import { qiniuCustomUploader } from '../../api/qiniuUploader'
import { auth } from '../../store/auth'

// VueFinder（及其 CSS/i18n）体积较大，仅文件管理用得到。为把它移出首屏入口 chunk，
// 改为在本懒加载视图里按需安装插件（首次进入文件管理时才加载 vuefinder）。
// 插件会 provide VueFinderOptions（i18n/locale），供下方 <VueFinder> 注入。
const app = getCurrentInstance()?.appContext.app
if (app && !app.__vuefinderInstalled) {
  app.use(VueFinderPlugin, { i18n: { zhCN }, locale: 'zhCN' })
  app.__vuefinderInstalled = true
}

// 进入本页面前路由守卫已确保已登录，token 必然存在。
const driver = createQiniuDriver(auth.token)

// 七牛是对象存储，没有压缩/解压能力，禁用 archive/unarchive；其余功能保留。
const features = { archive: false, unarchive: false }
</script>

<template>
  <div class="fm-wrap">
    <VueFinder
      id="dawnop-fm"
      :driver="driver"
      :features="features"
      :custom-uploader="qiniuCustomUploader"
      locale="zhCN"
    />
  </div>
</template>

<style scoped>
/* 铺满右栏：撑满后台内容区剩余高度（顶栏之下） */
.fm-wrap {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
/* 圆角卡片化，与后台其余页面的卡片观感一致 */
.fm-wrap :deep(.vuefinder) {
  flex: 1;
  min-height: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  box-shadow: var(--shadow-card);
}
</style>
