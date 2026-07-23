<!-- viz-name: Dawn · 效果变量在调用点实例化 -->
<script setup>
import { ref, computed } from 'vue'

const ioLambda = ref(false) // 传给 map 的 lambda 是否碰 IO
const declIo = ref(false) // process 的签名是否声明 !io

const lambdaLines = computed(() =>
  ioLambda.value
    ? ['  map(xs, fn(x) => {', '    println("{x}")', '    x * 2', '  })']
    : ['  map(xs, fn(x) => x * 2)'],
)

const verdict = computed(() => {
  if (ioLambda.value && !declIo.value) {
    return {
      ok: false,
      lines: [
        'error: function `process` is not declared !io but calls `map` (!io)',
        '  --> demo.dawn:2:3',
      ],
      note: '效果变量 e 在这个调用点被实例化成 io，于是这次 map 调用就是一次 IO 调用。签名没认账，编译器不放行。',
    }
  }
  if (!ioLambda.value && !declIo.value) {
    return {
      ok: true,
      lines: ['✓ 编译通过——process 是纯函数'],
      note: '同一个 map，这里 e ↦ pure，整条调用链没有碰 IO。纯函数意味着：测试它不用 mock，comptime 也能调它。',
    }
  }
  if (!ioLambda.value && declIo.value) {
    return {
      ok: true,
      lines: ['✓ 编译通过——但签名标宽了'],
      note: '声明的效果是上界：正文纯、签名标 !io 不算错，只是签名向所有调用者「谎报」了它可能碰 IO——调它的人也得跟着标。',
    }
  }
  return {
    ok: true,
    lines: ['✓ 编译通过——副作用写在签名上'],
    note: '正文确实碰了 IO，签名也认了账。任何人看一眼签名就知道：调 process 会碰外界。',
  }
})
</script>

<template>
  <div class="eff">
    <div class="sig-box">
      <div class="sig-label">map 的签名（stdlib，固定不变）</div>
      <div class="sig mono">
        fn map[T, U](xs: List[T], f: fn(T) -> U <b class="ev">!e</b>) -> List[U] <b class="ev">!e</b>
      </div>
      <div class="sig-note">效果变量 <b class="ev">!e</b>：map 自己不碰 IO，它的效果 = 你传进来的 f 的效果。</div>
    </div>

    <div class="controls">
      <div class="ctrl">
        <span class="ctrl-label">传给 map 的 lambda</span>
        <div class="seg">
          <button :class="{ on: !ioLambda }" @click="ioLambda = false">纯：x * 2</button>
          <button :class="{ on: ioLambda }" @click="ioLambda = true">带 IO：先 println</button>
        </div>
      </div>
      <div class="ctrl">
        <span class="ctrl-label">process 的签名</span>
        <div class="seg">
          <button :class="{ on: !declIo }" @click="declIo = false">不标效果</button>
          <button :class="{ on: declIo }" @click="declIo = true">标 !io</button>
        </div>
      </div>
    </div>

    <div class="code mono">
      <div>fn process(xs: List[Int]) -> List[Int]<b v-if="declIo" class="io"> !io</b> =</div>
      <div v-for="(l, i) in lambdaLines" :key="i">{{ l }}</div>
    </div>

    <div class="infer">
      <span class="step">lambda 的效果 = <b :class="ioLambda ? 'io' : 'pure'">{{ ioLambda ? 'io' : 'pure' }}</b></span>
      <span class="arr">→</span>
      <span class="step">实例化 <b class="ev">e</b> ↦ <b :class="ioLambda ? 'io' : 'pure'">{{ ioLambda ? 'io' : 'pure' }}</b></span>
      <span class="arr">→</span>
      <span class="step">这次 map 调用的效果 = <b :class="ioLambda ? 'io' : 'pure'">{{ ioLambda ? 'io' : 'pure' }}</b></span>
    </div>

    <div class="verdict mono" :class="verdict.ok ? 'ok' : 'bad'">
      <div v-for="(l, i) in verdict.lines" :key="i">{{ l }}</div>
    </div>
    <div class="foot">{{ verdict.note }}</div>
  </div>
</template>

<style scoped>
.eff {
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #1f2328;
}
.mono {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, 'Cascadia Mono', monospace;
}
.sig-box {
  background: #eef4ff;
  border: 1px solid #bcd4ff;
  border-radius: 9px;
  padding: 10px 14px;
}
.sig-label {
  font-size: 0.75rem;
  color: #6b7fa8;
  margin-bottom: 4px;
}
.sig {
  font-size: 0.85rem;
}
.sig-note {
  font-size: 0.8rem;
  color: #5b6b8c;
  margin-top: 5px;
}
.ev {
  color: #7c3aed;
}
.io {
  color: #cf1322;
}
.pure {
  color: #389e0d;
}
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin: 12px 0;
}
.ctrl {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ctrl-label {
  font-size: 0.82rem;
  color: #8a9099;
}
.seg {
  display: flex;
  border: 1px solid #d0d7de;
  border-radius: 7px;
  overflow: hidden;
}
.seg button {
  font: inherit;
  font-size: 0.82rem;
  padding: 4px 11px;
  border: none;
  background: #fff;
  color: #3c4149;
  cursor: pointer;
}
.seg button + button {
  border-left: 1px solid #d0d7de;
}
.seg button.on {
  background: #1677ff;
  color: #fff;
  font-weight: 600;
}
.code {
  background: #f6f8fa;
  border: 1px solid #e4e8ec;
  border-radius: 9px;
  padding: 10px 14px;
  font-size: 0.85rem;
  line-height: 1.8;
  white-space: pre;
  overflow-x: auto;
}
.infer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 12px 0;
  font-size: 0.83rem;
}
.step {
  background: #fff;
  border: 1px solid #e4e8ec;
  border-radius: 7px;
  padding: 4px 10px;
}
.arr {
  color: #b0b6bd;
}
.verdict {
  border-radius: 9px;
  padding: 10px 14px;
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
.foot {
  margin-top: 10px;
  font-size: 0.83rem;
  color: #8a9099;
  line-height: 1.7;
}
</style>
