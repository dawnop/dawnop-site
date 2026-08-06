<script setup>
// 分页器：前台列表页与后台管理页原本各抄一份同样的 el-pagination + .pager 外框。
// 条数不足一页时整块不渲染（原来是各处 v-if="total > size"）。
// variant 只有两档，对应现存的两种排布：
//   public = 居中 + 28px 上边距（首页 / 文章列表页 / 标签页 / 搜索页）
//   admin  = 右对齐 + 16px 上边距（后台管理页）
defineProps({
  total: { type: Number, required: true },
  pageSize: { type: Number, required: true },
  currentPage: { type: Number, required: true },
  variant: { type: String, default: 'public' },
  small: { type: Boolean, default: false },
})
defineEmits(['change'])
</script>

<template>
  <div v-if="total > pageSize" class="pager" :class="`pager-${variant}`">
    <el-pagination
      background
      :small="small"
      layout="prev, pager, next"
      :total="total"
      :page-size="pageSize"
      :current-page="currentPage"
      @current-change="$emit('change', $event)"
    />
  </div>
</template>

<style scoped>
.pager {
  display: flex;
}
.pager-public {
  justify-content: center;
  margin-top: 28px;
}
.pager-admin {
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
