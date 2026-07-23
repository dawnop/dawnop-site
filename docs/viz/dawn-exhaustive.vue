<!-- viz-name: Dawn · 穷尽性检查（usefulness 算法可玩版） -->
<script setup>
import { reactive, ref, computed } from 'vue'

// ---- 类型描述：ctors 为 null 表示开放类型（Int/Float，构造器集合无穷） ----
const FLOAT = { name: 'Float', ctors: null }
const INT = { name: 'Int', ctors: null }
const optOf = (t) => ({ name: `Option[${t.name}]`, ctors: { Some: [t], None: [] } })
const SHAPE = { name: 'Shape', ctors: { Circle: [FLOAT], Rect: [FLOAT, FLOAT], Point: [] } }
const OPT2 = optOf(optOf(INT))

// ---- 模式 ----
const W = { k: 'w' }
const C = (name, ...args) => ({ k: 'c', name, args })
const show = (p) =>
  p.k === 'w' ? '_' : p.args.length ? `${p.name}(${p.args.map(show).join(', ')})` : p.name

// ---- Maranget usefulness（与编译器 check/Exhaustive.kt 同一算法的 JS 复刻） ----
function specialize(matrix, name, arity) {
  const out = []
  for (const row of matrix) {
    const p = row[0]
    if (p.k === 'w') out.push([...Array(arity).fill(W), ...row.slice(1)])
    else if (p.name === name) out.push([...p.args, ...row.slice(1)])
  }
  return out
}
const defaultMatrix = (matrix) => matrix.filter((r) => r[0].k === 'w').map((r) => r.slice(1))
const seenCtors = (matrix) =>
  new Set(matrix.map((r) => r[0]).filter((p) => p.k === 'c').map((p) => p.name))

// 模式向量 q 相对矩阵是否「有用」：存在一个值，先前所有行都接不住、q 能接住
function useful(matrix, q, types) {
  if (q.length === 0) return matrix.length === 0
  const t = types[0]
  const p = q[0]
  if (p.k === 'c') {
    const argTypes = t.ctors[p.name]
    return useful(
      specialize(matrix, p.name, argTypes.length),
      [...p.args, ...q.slice(1)],
      [...argTypes, ...types.slice(1)],
    )
  }
  const seen = seenCtors(matrix)
  const all = t.ctors ? Object.keys(t.ctors) : null
  if (all && all.every((c) => seen.has(c))) {
    return all.some((c) =>
      useful(
        specialize(matrix, c, t.ctors[c].length),
        [...t.ctors[c].map(() => W), ...q.slice(1)],
        [...t.ctors[c], ...types.slice(1)],
      ),
    )
  }
  return useful(defaultMatrix(matrix), q.slice(1), types.slice(1))
}

// 构造一个没被任何行接住的反例；全接住则返回 null
function witness(matrix, types) {
  if (types.length === 0) return matrix.length === 0 ? [] : null
  const t = types[0]
  const seen = seenCtors(matrix)
  const all = t.ctors ? Object.keys(t.ctors) : null
  if (all && all.every((c) => seen.has(c))) {
    for (const c of all) {
      const argTypes = t.ctors[c]
      const sub = witness(specialize(matrix, c, argTypes.length), [...argTypes, ...types.slice(1)])
      if (sub) return [C(c, ...sub.slice(0, argTypes.length)), ...sub.slice(argTypes.length)]
    }
    return null
  }
  const sub = witness(defaultMatrix(matrix), types.slice(1))
  if (!sub) return null
  let head = W
  if (all) {
    const miss = all.find((c) => !seen.has(c))
    head = C(miss, ...t.ctors[miss].map(() => W))
  }
  return [head, ...sub]
}

// ---- 两个场景 ----
const scenarios = reactive({
  shape: {
    tab: 'Shape（三个构造器）',
    head: 'match s {',
    type: SHAPE,
    rows: [
      { pat: C('Circle', W), src: 'Circle(r)  -> 3.14159 * r * r', on: true },
      { pat: C('Rect', W, W), src: 'Rect(w, h) -> w * h', on: true },
      { pat: C('Point'), src: 'Point      -> 0.0', on: true },
      { pat: W, src: '_          -> -1.0', on: false },
    ],
  },
  opt: {
    tab: 'Option[Option[Int]]（嵌套）',
    head: 'match o {',
    type: OPT2,
    rows: [
      { pat: C('Some', C('Some', W)), src: 'Some(Some(v)) -> v', on: true },
      { pat: C('Some', C('None')), src: 'Some(None)    -> -1', on: true },
      { pat: C('None'), src: 'None          -> -2', on: true },
      { pat: C('Some', W), src: 'Some(_)       -> 0', on: false },
    ],
  },
})
const cur = ref('shape')
const scen = computed(() => scenarios[cur.value])

const analysis = computed(() => {
  const type = scen.value.type
  const acc = []
  const redundant = []
  for (const r of scen.value.rows) {
    if (!r.on) {
      redundant.push(false)
      continue
    }
    redundant.push(!useful(acc.map((p) => [p]), [r.pat], [type]))
    acc.push(r.pat)
  }
  const w = witness(acc.map((p) => [p]), [type])
  return { redundant, missing: w ? show(w[0]) : null }
})
</script>

<template>
  <div class="exh">
    <div class="tabs">
      <button
        v-for="(s, k) in scenarios"
        :key="k"
        class="tab"
        :class="{ on: cur === k }"
        @click="cur = k"
      >
        {{ s.tab }}
      </button>
    </div>

    <div class="code">
      <div class="line head">{{ scen.head }}</div>
      <div
        v-for="(r, i) in scen.rows"
        :key="i"
        class="line arm"
        :class="{ off: !r.on, dead: analysis.redundant[i] }"
        @click="r.on = !r.on"
      >
        <span class="box">{{ r.on ? '✓' : '' }}</span>
        <span class="src">{{ r.src }}</span>
        <span v-if="analysis.redundant[i]" class="badge">永远匹配不到</span>
      </div>
      <div class="line head">}</div>
    </div>

    <div class="verdict" :class="analysis.missing ? 'bad' : 'ok'">
      <template v-if="analysis.missing">
        <div class="v-line"><b>error:</b> non-exhaustive match, missing: {{ analysis.missing }}</div>
        <div class="v-hint">= hint: add arms for the missing constructors, or a catch-all _</div>
      </template>
      <template v-else>
        <div class="v-line">✓ match 穷尽——每个可能的值都有分支接住</div>
      </template>
    </div>

    <div class="foot">
      点分支行启用 / 禁用它。这里跑的就是编译器里那份 usefulness 算法（Maranget 2007）的
      JS 复刻：一行「有用」= 存在某个值只有它能接住；穷尽 = 在所有分支后面补一个 _ 之后，这个 _
      没用。反例（missing 后面那个模式）也是算法顺手构造出来的，不是枚举。
    </div>
  </div>
</template>

<style scoped>
.exh {
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #1f2328;
}
.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}
.tab {
  font: inherit;
  font-size: 0.85rem;
  padding: 5px 12px;
  border: 1px solid #d0d7de;
  border-radius: 7px;
  background: #fff;
  color: #3c4149;
  cursor: pointer;
  transition: all 0.15s;
}
.tab.on {
  border-color: #1677ff;
  color: #1677ff;
  background: #eef4ff;
  font-weight: 600;
}
.code {
  background: #f6f8fa;
  border: 1px solid #e4e8ec;
  border-radius: 9px;
  padding: 10px 14px;
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, 'Cascadia Mono', monospace;
  font-size: 0.85rem;
  line-height: 1.9;
}
.line.head {
  color: #8a9099;
}
.arm {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-left: 4px;
  cursor: pointer;
  border-radius: 6px;
  user-select: none;
}
.arm:hover {
  background: #eef1f4;
}
.box {
  width: 16px;
  height: 16px;
  flex: none;
  border: 1.5px solid #b8c0c8;
  border-radius: 4px;
  background: #fff;
  font-size: 11px;
  line-height: 14px;
  text-align: center;
  color: #1677ff;
  font-weight: 700;
}
.arm:not(.off) .box {
  border-color: #1677ff;
}
.src {
  white-space: pre;
}
.arm.off .src {
  color: #b0b6bd;
  text-decoration: line-through;
  text-decoration-color: #d0d7de;
}
.arm.dead .src {
  color: #b0851f;
}
.badge {
  font-family: -apple-system, 'PingFang SC', sans-serif;
  font-size: 0.72rem;
  color: #b0851f;
  background: #fff8e6;
  border: 1px solid #f0dfa8;
  border-radius: 5px;
  padding: 1px 7px;
}
.verdict {
  margin-top: 10px;
  border-radius: 9px;
  padding: 10px 14px;
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, 'Cascadia Mono', monospace;
  font-size: 0.83rem;
  line-height: 1.7;
}
.verdict.ok {
  background: #f0f9eb;
  border: 1px solid #cde8b8;
  color: #389e0d;
}
.verdict.bad {
  background: #fef0f0;
  border: 1px solid #f5c6c6;
  color: #cf1322;
}
.v-hint {
  color: #a05656;
}
.foot {
  margin-top: 10px;
  font-size: 0.83rem;
  color: #8a9099;
  line-height: 1.7;
}
</style>
