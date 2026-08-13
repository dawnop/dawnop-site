#!/usr/bin/env node
// 文件管理器名称判词的门禁：跑 frontend/src/utils/fmPath.js 的行为，外加一条
// 接线检查（判词有没有真的接在生产调用点上）。
//
// 为什么需要它：这四条规则合起来只有十几行代码，任何一条被改回去都不会让
// lint / build / 现有测试变红——它们是「少写一个 .trim()」这种减法，没有语法
// 痕迹。本仓的纪律是「新规则必须有能实际转红的 production mutant」，所以下面
// 每条规则都标了它的负控：把那处生产代码改坏，这条且只有这条会红。
//
// 依赖：无。故意的——fmPath.js 不 import 任何东西，本脚本也是，于是它能排在
// CI 的 npm ci 之前裸跑，一秒出结果。路径全部相对本文件解析，从哪个目录跑都行。
//
// 用法：node scripts/check-fm-path.mjs

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const UTILS = new URL('../frontend/src/utils/fmPath.js', import.meta.url)
const COMPOSABLE = new URL('../frontend/src/composables/useFileManager.js', import.meta.url)

const { createName, renamePlan, FmNameError } = await import(UTILS)

// ---------- 迷你断言 ----------
// 每条规则拿到一个收集器，失败不抛、只记账：一次跑完要能看清「红了几条规则、
// 各红在哪一例」，而不是撞上第一个失败就退出（正交性得靠一次全跑才看得出来）。
function collector(failures, ruleId) {
  const fail = (what, expected, actual) => failures.push({ ruleId, what, expected, actual })
  const show = (v) => (typeof v === 'string' ? JSON.stringify(v) : JSON.stringify(v) || String(v))
  return {
    // 深比较用 JSON 串：这里的值只有字符串和 {send, name} 这种平坦对象。
    eq(actual, expected, what) {
      if (JSON.stringify(actual) !== JSON.stringify(expected)) {
        fail(what, show(expected), show(actual))
      }
    },
    rejects(fn, what) {
      let value
      try {
        value = fn()
      } catch (e) {
        if (!(e instanceof FmNameError)) fail(what, '抛 FmNameError', `抛 ${e?.name}: ${e?.message}`)
        return
      }
      fail(what, '抛 FmNameError', `返回 ${show(value)}`)
    },
    ok(cond, what, expected, actual) {
      if (!cond) fail(what, expected, actual)
    },
  }
}

// ---------- 接线检查用的取函数体 ----------
// 花括号配平，够用：这两个函数体里没有出现在字符串/注释里的花括号，
// 真出现了这里会取到一段错的文本、断言随之变红，不会静默放过。
function bodyOf(src, header, where) {
  const start = src.indexOf(header)
  if (start < 0) throw new Error(`${where}: 找不到 ${header}`)
  let i = src.indexOf('{', start)
  if (i < 0) throw new Error(`${where}: ${header} 后没有函数体`)
  let depth = 0
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') depth++
    else if (src[j] === '}' && --depth === 0) return src.slice(i, j + 1)
  }
  throw new Error(`${where}: ${header} 的函数体没有闭合`)
}

// ---------- 四条规则 + 一条接线 ----------
const rules = [
  {
    id: 'create-no-trim',
    desc: '新建不 trim：用户敲的名字原样发给后端，全空格也是合法名称',
    // 负控：frontend/src/utils/fmPath.js 的 createName 改成
    //       `return requireName(raw).trim()`
    run(t) {
      t.eq(createName(' a '), ' a ', 'createName(" a ") 保留首尾空格')
      t.eq(createName('a '), 'a ', 'createName("a ") 保留尾部空格')
      t.eq(createName(' 新文件夹'), ' 新文件夹', 'createName(" 新文件夹") 保留首部空格')
      t.eq(createName('   '), '   ', 'createName("   ") 全空格是合法名称')
      t.eq(createName('\t'), '\t', 'createName("\\t") 制表符同理')
    },
  },
  {
    id: 'rename-no-trim',
    desc: '重命名不 trim：发出去的名字原样，加空格 / 去空格都算改了名',
    // 负控：frontend/src/utils/fmPath.js 的 renamePlan 改成
    //       `const name = requireName(raw).trim()`
    run(t) {
      t.eq(renamePlan('x', ' a '), { send: true, name: ' a ' }, 'rename 到 " a " 原样发')
      t.eq(renamePlan('x', '   '), { send: true, name: '   ' }, 'rename 到 "   " 原样发')
      t.eq(renamePlan('a', ' a '), { send: true, name: ' a ' }, '"a" → " a " 不是同名，要发')
      t.eq(renamePlan(' a ', 'a'), { send: true, name: 'a' }, '" a " → "a" 不是同名，要发')
      t.eq(renamePlan('a.txt', 'a.txt '), { send: true, name: 'a.txt ' }, '只多一个尾空格也要发')
    },
  },
  {
    id: 'empty-rejected',
    desc: '只拒两种输入：非字符串、真正的空串（trim 后为空不算空）',
    // 负控：frontend/src/utils/fmPath.js 的 requireName 删掉
    //       `if (raw === '') throw ...` 那一行
    run(t) {
      t.rejects(() => createName(''), 'createName("") 被拒')
      t.rejects(() => renamePlan('x', ''), 'renamePlan(_, "") 被拒')
      t.rejects(() => createName(undefined), 'createName(undefined) 被拒')
      t.rejects(() => createName(null), 'createName(null) 被拒')
      t.rejects(() => createName(42), 'createName(42) 被拒')
      t.rejects(() => renamePlan('x', ['a']), 'renamePlan(_, ["a"]) 被拒')
      // 与上面成对：拒的是空串本身，不是「trim 后为空」。
      t.eq(createName(' '), ' ', 'createName(" ") 不被当成空串')
    },
  },
  {
    id: 'identity-skip',
    desc: '同 identity 的原样重命名不发请求',
    // 负控：frontend/src/utils/fmPath.js 的 isSameName 改成 `return false`
    //
    // 这里的名字都取 trim 前后一样的（"a"、"a.txt"、"文件夹"）。像
    // renamePlan("   ", "   ") 那种「全空格同名」是本条与 rename-no-trim 的
    // 交集：两条规则的负控都能红它，放进任一条都会让正交性失真，故不放。
    run(t) {
      t.eq(renamePlan('a', 'a'), { send: false }, '改成原名不发请求')
      t.eq(renamePlan('a.txt', 'a.txt'), { send: false }, '带扩展名同理')
      t.eq(renamePlan('文件夹', '文件夹'), { send: false }, '中文名同理')
    },
  },
  {
    id: 'call-site-no-trim',
    desc: '接线：生产调用点走判词，且自己不 trim',
    // 负控：frontend/src/composables/useFileManager.js 的 newFolder 改成
    //       `createName(value.trim())`
    //
    // 前四条只证明 fmPath.js 自己是对的。判词摆在那儿而调用点自己先 trim 一道，
    // 前四条照样全绿——这条是那个洞的盖子。它读源码而不是跑行为：newFolder /
    // doRename 拖着 vue + element-plus，在 node 里裸跑不起来，为此把两个函数
    // 拆成可注入的形状，代价比它挡住的风险大。
    run(t) {
      const src = readFileSync(fileURLToPath(COMPOSABLE), 'utf8')
      const cases = [
        ['async function newFolder()', 'createName('],
        ['async function doRename(row)', 'renamePlan('],
      ]
      for (const [header, callee] of cases) {
        const body = bodyOf(src, header, 'useFileManager.js')
        const fn = header.replace('async function ', '').replace(/\(.*/, '')
        t.ok(body.includes(callee), `${fn} 调用 ${callee}`, `出现 ${callee}`, '没出现')
        t.ok(!body.includes('.trim('), `${fn} 自己不 trim`, '不出现 .trim(', '出现了 .trim(')
      }
    },
  },
]

// ---------- 跑 ----------
const failures = []
for (const rule of rules) {
  const before = failures.length
  try {
    rule.run(collector(failures, rule.id))
  } catch (e) {
    failures.push({ ruleId: rule.id, what: '规则自身跑挂了', expected: '正常跑完', actual: String(e) })
  }
  const bad = failures.length - before
  console.log(`${bad ? 'FAIL' : 'PASS'}  ${rule.id.padEnd(18)} ${rule.desc}${bad ? ` （${bad} 例红）` : ''}`)
}

if (failures.length) {
  console.log('')
  for (const f of failures) {
    console.log(`  [${f.ruleId}] ${f.what}\n      期望 ${f.expected}\n      实际 ${f.actual}`)
  }
  const reds = new Set(failures.map((f) => f.ruleId))
  console.log(`\n${failures.length} 例失败，涉及 ${reds.size} 条规则：${[...reds].join(', ')}`)
  process.exit(1)
}

console.log(`\n${rules.length} 条规则全绿`)
