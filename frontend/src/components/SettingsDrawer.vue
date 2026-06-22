<script setup>
// 右侧设置抽屉：从右侧滑入覆盖，不挤压主区。用在文章/页面编辑页。
defineProps({
  modelValue: { type: Boolean, default: false },
  title: { type: String, default: '设置' },
})
const emit = defineEmits(['update:modelValue'])
const close = () => emit('update:modelValue', false)
</script>

<template>
  <Transition name="dw-fade">
    <div v-if="modelValue" class="drawer-backdrop" @click="close"></div>
  </Transition>
  <Transition name="dw-slide">
    <aside v-if="modelValue" class="drawer" role="dialog" aria-modal="true">
      <header class="drawer-head">
        <span class="drawer-title">{{ title }}</span>
        <button class="drawer-x" type="button" title="关闭" @click="close">✕</button>
      </header>
      <div class="drawer-body"><slot /></div>
      <footer v-if="$slots.footer" class="drawer-foot"><slot name="footer" /></footer>
    </aside>
  </Transition>
</template>

<style scoped>
.drawer-backdrop {
  position: fixed;
  top: 56px; /* 后台顶栏高度之下 */
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.18);
  z-index: 90;
}
.drawer {
  position: fixed;
  top: 56px;
  right: 0;
  bottom: 0;
  width: 340px;
  max-width: 86vw;
  background: #fff;
  border-left: 1px solid var(--border);
  box-shadow: -8px 0 24px rgba(0, 0, 0, 0.08);
  z-index: 100;
  display: flex;
  flex-direction: column;
}
.drawer-head {
  flex-shrink: 0;
  height: 52px;
  padding: 0 16px 0 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--border);
}
.drawer-title {
  font-weight: 600;
  color: var(--fg);
}
.drawer-x {
  border: none;
  background: transparent;
  color: var(--muted);
  font-size: 1rem;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
}
.drawer-x:hover {
  background: #f0f2f5;
  color: var(--fg);
}
.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px 24px;
}
.drawer-foot {
  flex-shrink: 0;
  padding: 14px 20px;
  border-top: 1px solid var(--border);
}

/* 过渡 */
.dw-fade-enter-active,
.dw-fade-leave-active {
  transition: opacity 0.2s ease;
}
.dw-fade-enter-from,
.dw-fade-leave-to {
  opacity: 0;
}
.dw-slide-enter-active,
.dw-slide-leave-active {
  transition: transform 0.22s ease;
}
.dw-slide-enter-from,
.dw-slide-leave-to {
  transform: translateX(100%);
}
</style>
