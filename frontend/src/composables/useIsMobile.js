import { ref } from 'vue'

// 全站统一的响应式断点检测。断点：≤768px = 移动，≤480px = 小屏。
// 用 matchMedia + 单例监听（每个断点只建一个 MediaQueryList，全 App 复用），
// 返回响应式布尔 ref。SPA 生命周期内常驻，无需清理。

function createMedia(query) {
  const mql = window.matchMedia(query)
  const state = ref(mql.matches)
  const handler = (e) => { state.value = e.matches }
  if (mql.addEventListener) mql.addEventListener('change', handler)
  else mql.addListener(handler) // 旧 Safari 兜底
  return state
}

let _mobile = null
let _small = null

/** ≤768px：手机 / 竖屏平板。 */
export function useIsMobile() {
  if (!_mobile) _mobile = createMedia('(max-width: 768px)')
  return _mobile
}

/** ≤480px：小屏手机（用于网格列数、字号等更细的调整）。 */
export function useIsSmall() {
  if (!_small) _small = createMedia('(max-width: 480px)')
  return _small
}
