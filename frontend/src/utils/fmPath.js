// 文件管理器的名称判词（新建 / 重命名共用）。
//
// 为什么单独成文件而不是留在 useFileManager.js 里：这些规则由
// scripts/check-fm-path.mjs 直接 import 并跑负控，而那个脚本要能在没装
// node_modules 的环境里裸跑（CI 里它排在 npm ci 之前）。所以本文件
// **不许 import 任何东西**——vue、element-plus 都不行，只留纯函数。
//
// 一条总纲：名称就是 identity，前端不改写它。用户敲的 " a " 就是 " a "，
// "   "（全是空格）是合法名称。旧实现在 create 与 rename 两处都 value.trim()，
// 把「trim 后为空」和「真正的空串」混成一类，于是：
//   - " a " 被静默改写成 "a"，用户看到的名字不是他敲的那个；
//   - "   " 被改写成 ""，再被当成「没填」；
//   - 改名时拿 trim 后的名字跟原名比 identity，"a" → " a " 被误判成没改，
//     请求连发都不发，用户以为改了、其实什么都没发生。
// 现在只拒两种输入：非字符串、真正的空串。其余原样交给后端判——
// 名称合不合法是后端的事（它认得单段名、重名、`.`/`..`），前端别抢着替它裁。

export class FmNameError extends Error {
  constructor(message) {
    super(message)
    this.name = 'FmNameError'
  }
}

// 共享的最小判词：只拦非字符串与真正的空串，不 trim、不改写。
function requireName(raw) {
  if (typeof raw !== 'string') throw new FmNameError('名称必须是文本')
  if (raw === '') throw new FmNameError('名称不能为空')
  return raw
}

// 新建用的名称。独立成一个函数而不是让调用方直接用 requireName：
// create 与 rename 的规则要能被各自单独改坏，否则负控互相纠缠、
// 分不出「哪条规则在守着什么」（见 scripts/check-fm-path.mjs 的四条负控）。
export function createName(raw) {
  return requireName(raw)
}

// 逐字比较，不 trim。这行是判据 4 的全部：一旦这里 trim，
// "a" → " a " 就会被当成同名短路掉，用户的改名被静默吞掉。
export function isSameName(a, b) {
  return a === b
}

// 重命名的决策：要么不发请求（名字逐字没变），要么发这个**原样**的名字。
// 返回 plan 而不是直接返回名字或 null，是为了让「不发请求」在调用点
// 是一句显式的 `if (!plan.send) return`，而不是一个要靠注释解释的空值。
export function renamePlan(currentName, raw) {
  const name = requireName(raw)
  if (isSameName(name, currentName)) return { send: false }
  return { send: true, name }
}
