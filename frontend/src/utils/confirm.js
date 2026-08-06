// 危险操作（删除/清理）的确认弹窗：各后台页原本各抄一份同样的 ElMessageBox.confirm 配置。
// 显式 import 是有意的：vite.config.js 的 unplugin-element-plus 会把它改写成带样式的深引入，
// 不会退化成无样式裸框（见该文件注释）。
import { ElMessageBox } from 'element-plus'

// 返回 Promise<boolean>：确认 true / 取消 false，调用方不必再 try/catch 一遍取消。
// opts.confirmText 确认按钮文案（默认「删除」）；opts.danger=false 时不给确认按钮上红色类。
export function confirmDanger(message, title, opts = {}) {
  const { confirmText = '删除', cancelText = '取消', danger = true } = opts
  return ElMessageBox.confirm(message, title, {
    type: 'warning',
    confirmButtonText: confirmText,
    cancelButtonText: cancelText,
    ...(danger ? { confirmButtonClass: 'el-button--danger' } : {}),
  }).then(
    () => true,
    () => false,
  )
}
