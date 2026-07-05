// 未保存离开拦截：编辑页共用。
// 传入 serialize()（把当前表单序列化成字符串），返回：
//   dirty         —— 与上次快照是否不同（脏）
//   takeSnapshot()—— 记录当前状态为「已保存基线」（加载完数据 / 保存成功后调用）
//   markSaved()   —— 标记刚保存过，随后跳转不再被拦截
// 内部接管 window.beforeunload 与路由 onBeforeRouteLeave（脏则弹「放弃修改」确认框）。
import { computed, onBeforeUnmount } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessageBox } from 'element-plus'

export function useUnsavedGuard(serialize) {
  let snapshot = serialize()
  let justSaved = false

  const dirty = computed(() => serialize() !== snapshot)

  function takeSnapshot() {
    snapshot = serialize()
    justSaved = false
  }
  function markSaved() {
    justSaved = true
  }

  function beforeUnload(e) {
    if (dirty.value && !justSaved) {
      e.preventDefault()
      e.returnValue = ''
    }
  }
  window.addEventListener('beforeunload', beforeUnload)
  onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))

  onBeforeRouteLeave(async () => {
    if (!dirty.value || justSaved) return true
    try {
      await ElMessageBox.confirm('有未保存的修改，确定离开吗？', '放弃修改', {
        type: 'warning',
        confirmButtonText: '放弃',
        cancelButtonText: '继续编辑',
      })
      return true
    } catch {
      return false
    }
  })

  return { dirty, takeSnapshot, markSaved }
}
