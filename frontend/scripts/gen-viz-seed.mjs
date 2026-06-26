// 生成内置可视化的「种子数据」：把 scripts/viz-seed/*.vue 编译成 {slug,name,source,compiled,style}，
// 写入 backend/scripts/seed_viz.json，供后端 seed_admin.py 初始化进数据库。
//
// 用法（在 frontend/ 目录）：node scripts/gen-viz-seed.mjs
// 改了某个种子 .vue 后重跑本脚本并提交 seed_viz.json 即可。编译逻辑与浏览器内 compileSfc 共用同一
// 转换函数（src/viz/sfcTransform.js），保证产物一致。
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { parse, compileScript, compileStyle } from '@vue/compiler-sfc'
import { esmToFactoryBody } from '../src/viz/sfcTransform.js'

const here = path.dirname(fileURLToPath(import.meta.url))
const SEED_DIR = path.join(here, 'viz-seed')
const OUT = path.resolve(here, '../../backend/scripts/seed_viz.json')

function compile(source, slug) {
  const id = slug
  const scopeId = `data-v-${slug}`
  const filename = `${slug}.vue`
  const { descriptor, errors } = parse(source, { filename })
  if (errors?.length) throw new Error(errors[0].message || String(errors[0]))
  const script = compileScript(descriptor, { id, inlineTemplate: true })
  const compiled = esmToFactoryBody(script.content, scopeId)
  let style = ''
  for (const s of descriptor.styles) {
    const r = compileStyle({ source: s.content, id, scoped: s.scoped, filename })
    if (r.errors?.length) throw new Error(r.errors[0].message || String(r.errors[0]))
    style += r.code + '\n'
  }
  return { compiled, style }
}

const out = []
for (const f of fs.readdirSync(SEED_DIR).filter((f) => f.endsWith('.vue'))) {
  const slug = path.basename(f, '.vue')
  const raw = fs.readFileSync(path.join(SEED_DIR, f), 'utf8')
  const nameMatch = raw.match(/<!--\s*viz-name:\s*(.+?)\s*-->/)
  const name = nameMatch ? nameMatch[1] : slug
  // 去掉仅用于本脚本的 name 注释，存入库的 source 保持干净
  const source = raw.replace(/<!--\s*viz-name:.*?-->\s*\n?/, '').replace(/^\s+/, '')
  const { compiled, style } = compile(source, slug)
  out.push({ slug, name, source, compiled, style })
  console.log(`✓ ${slug}（${name}）— compiled ${compiled.length}B, style ${style.length}B`)
}

fs.writeFileSync(OUT, JSON.stringify(out, null, 2) + '\n')
console.log(`已写入 ${out.length} 个 → ${OUT}`)
