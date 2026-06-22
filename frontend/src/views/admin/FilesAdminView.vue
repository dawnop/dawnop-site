<script setup>
import { VueFinder } from 'vuefinder'
import { createQiniuDriver } from '../../api/vuefinderDriver'
import { qiniuCustomUploader } from '../../api/qiniuUploader'
import { auth } from '../../store/auth'

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
.fm-wrap :deep(.vuefinder) {
  flex: 1;
  min-height: 0;
}
</style>
