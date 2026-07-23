<!-- viz-name: Dawn · 自举链（种子 → stage1 → stage2 → stage3 → 固定点） -->
<script setup>
import { ref, computed } from 'vue'

const nodes = {
  seed: {
    label: '种子',
    sub: 'Kotlin · v0.6.0',
    kind: 'kotlin',
    kotlin: true,
    caption: '信任链的根：最后一个 Kotlin 写的编译器，v0.6.0 冻结、release 归档的那个 jar。学 Go 永久保留 go1.4——种子只有一条义务：永远能编译当前的 Dawn 源。',
    art: `# 信任链的根
scripts/seed-release.txt  →  v0.6.0 / dawn.jar   (Kotlin)

义务只有一条：
  种子必须始终能编译当前的 selfhost/ 源
  （失去这个能力算最高级别故障）`,
  },
  a: {
    label: '编译器 A',
    sub: 'stage 1 产物',
    kind: 'dawn',
    kotlin: true,
    caption: 'stage 1：种子（Kotlin）编译 Dawn 版编译器的源码，得到 A——一个用 Dawn 写、跑在 JVM 上的编译器。这一步 Kotlin 还在场，A 是它引导出来的。',
    art: `$ java -jar seed.jar  build selfhost -o A.jar
           └ Kotlin          └ Dawn 编译器的源码

得到 A：用 Dawn 写、跑在 JVM 上的编译器。
Kotlin 仍在链条里——A 是被它引导出来的。`,
  },
  b: {
    label: '编译器 B',
    sub: 'stage 2 · 规范产物',
    kind: 'dawn',
    kotlin: false,
    caption: 'stage 2：用 A 编译它自己的源码，发射出全部 433 个类。B 是「规范产物」——一个 Dawn 编译器发射出的自己。到这一步，Kotlin 已从画面里消失。',
    art: `$ java -jar A.jar     build selfhost -o B.jar
           └ Dawn (A)        └ 同一份 Dawn 源

A 编译它自己 → 发射 433 个类。
B = 规范产物：Dawn 编译器发射出的自己。`,
  },
  c: {
    label: '编译器 C',
    sub: 'stage 3 产物',
    kind: 'dawn',
    kotlin: false,
    caption: 'stage 3：再用 stage2 刚发射的那批类（B）编译同一份源码，得到 C。然后 cmp B C——逐字节相同。编译器发射出的自己，再拿去发射自己，结果不动了：这就是自举固定点。',
    art: `$ java -jar B.jar     build selfhost -o C.jar
           └ Dawn (B)        └ 同一份 Dawn 源

$ cmp B.jar C.jar
                        # 逐字节相同 ✓

固定点：C 只由 Dawn 源决定，
跟当初是谁把它引导出来的无关。`,
  },
}

const order = ['seed', 'a', 'b', 'c']
const steps = { a: 'stage 1', b: 'stage 2', c: 'stage 3' }
const sel = ref('c')
const cur = computed(() => nodes[sel.value])
</script>

<template>
  <div class="chain">
    <div class="flow">
      <template v-for="(k, i) in order" :key="k">
        <span v-if="i > 0" class="edge">
          <span class="step">{{ steps[k] }}</span>
          <span class="arr">→</span>
        </span>
        <button
          class="node"
          :class="[nodes[k].kind, { on: sel === k, eq: k === 'b' || k === 'c' }]"
          @click="sel = k"
        >
          <span class="n-label">{{ nodes[k].label }}</span>
          <span class="n-sub">{{ nodes[k].sub }}</span>
        </button>
      </template>
    </div>
    <div class="eqbar">
      <span class="eqbadge">B <span class="eqsym">≡</span> C · 逐字节</span>
    </div>

    <div class="panel">
      <div class="tag" :class="cur.kotlin ? 'in' : 'out'">
        {{ cur.kotlin ? 'Kotlin 在场' : 'Kotlin 已退场' }}
      </div>
      <pre class="art">{{ cur.art }}</pre>
    </div>
    <div class="caption">{{ cur.caption }}</div>
  </div>
</template>

<style scoped>
.chain {
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #1f2328;
}
.flow {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 6px;
}
.node {
  font: inherit;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  padding: 7px 13px;
  border: 1px solid #d0d7de;
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
  transition: all 0.15s;
}
.node:hover {
  border-color: #91b6f5;
}
.node.kotlin {
  border-style: dashed;
  background: #fafafa;
}
.node.on {
  border-color: #1677ff;
  background: #eef4ff;
}
.node.kotlin.on {
  border-style: solid;
}
.n-label {
  font-size: 0.84rem;
  font-weight: 600;
  color: #3c4149;
}
.node.on .n-label {
  color: #1677ff;
}
.n-sub {
  font-size: 0.7rem;
  color: #8a9099;
}
.edge {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  line-height: 1;
  flex: none;
  padding: 0 2px;
}
.step {
  font-size: 0.62rem;
  color: #a4abb3;
  margin-bottom: 1px;
}
.arr {
  color: #b0b6bd;
  font-size: 0.95rem;
}
.eqbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
  padding-right: 2px;
}
.eqbadge {
  font-size: 0.7rem;
  color: #1677ff;
  background: #eef4ff;
  border: 1px solid #cfe0ff;
  border-radius: 20px;
  padding: 2px 10px;
}
.eqsym {
  font-weight: 700;
}
.panel {
  position: relative;
}
.tag {
  position: absolute;
  top: 9px;
  right: 10px;
  font-size: 0.66rem;
  padding: 2px 8px;
  border-radius: 12px;
  letter-spacing: 0.02em;
}
.tag.in {
  color: #9a6a00;
  background: #fff4d6;
}
.tag.out {
  color: #197a3d;
  background: #dff5e6;
}
.art {
  margin: 0;
  background: #f6f8fa;
  border: 1px solid #e4e8ec;
  border-radius: 9px;
  padding: 12px 14px;
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, 'Cascadia Mono', monospace;
  font-size: 0.8rem;
  line-height: 1.65;
  overflow-x: auto;
  min-height: 8.5em;
}
.caption {
  margin-top: 10px;
  font-size: 0.83rem;
  color: #8a9099;
  line-height: 1.7;
}
</style>
