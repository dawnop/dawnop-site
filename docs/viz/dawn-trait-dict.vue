<!-- viz-name: Dawn · 字典传递（a < b 编译成什么） -->
<script setup>
import { ref, computed } from 'vue'

const scene = ref('generic')

const scenes = [
  { key: 'scalar', label: '具体标量' },
  { key: 'concrete', label: '具体自定义类型' },
  { key: 'generic', label: '泛型 [T: Ord]' },
]

const view = computed(() => {
  switch (scene.value) {
    case 'scalar':
      return {
        code: ['fn smaller(a: Int, b: Int) -> Bool = a < b'],
        hl: [],
        steps: ['a、b 的类型 = Int', '原生有序，不查 trait', '走老的快路径'],
        out: ['LLOAD a', 'LLOAD b', 'LCMP        # 一条比较指令，与 trait 无关'],
        note: 'Int/Float/String 的比较和从前一模一样——桥接只对「其他类型」生效，老代码的字节码一个字节没变。',
      }
    case 'concrete':
      return {
        code: [
          'impl Ord[Point] {',
          '  fn cmp(a: Point, b: Point) -> Int = a.x - b.x',
          '}',
          '',
          'fn smaller(p: Point, q: Point) -> Bool = p < q',
        ],
        hl: [4],
        steps: ['p、q 的类型 = Point（具体）', '查一致性表：Ord[Point] 全程序唯一', '拿到 impl，调用点去虚化'],
        out: [
          'ALOAD p',
          'ALOAD q',
          'INVOKESTATIC dawn$impl$Ord$Point$cmp   # 直呼 impl 静态方法',
          'LCONST_0 / LCMP                        # 结果与 0 比较',
        ],
        note: '一致性在这里兑现成性能：impl 唯一，所以编译器敢在编译期把动态分发整个抹掉。derive Ord 生成的 cmp 走的也是同一条路。',
      }
    default:
      return {
        code: [
          'fn max2[T: Ord](a: T, b: T) -> T =',
          '  if a < b { b } else { a }',
          '',
          'max2(p, q)          # 调用点 1：T = Point',
          '',
          'fn pick[T: Ord](x: T, y: T) -> T =',
          '  max2(x, y)        # 调用点 2：T 还是类型参数',
        ],
        hl: [1],
        steps: ['函数体只编译一份（擦除）', '每个 [T: Ord] 配一个隐藏字典参数', 'a < b 对着字典调 cmp'],
        out: [
          '# max2 实际签名：max2(a, b, dict)',
          'ALOAD dict',
          'INVOKEINTERFACE dawn/tr/Ord.cmp   # 唯一残留的动态分发',
          '',
          '# 调用点 1：max2(p, q, GETSTATIC Ord$Point.INSTANCE)',
          '# 调用点 2：max2(x, y, dict)      # 转发 pick 自己的字典',
        ],
        note: '字典在实现里就是一个合成局部变量——所以 lambda 里写 a < b 时，闭包捕获机制原样把字典捎进去，没有任何特判。',
      }
  }
})
</script>

<template>
  <div class="dict">
    <div class="controls">
      <span class="ctrl-label">a &lt; b 出现在哪儿？</span>
      <div class="seg">
        <button
          v-for="s in scenes"
          :key="s.key"
          :class="{ on: scene === s.key }"
          @click="scene = s.key"
        >{{ s.label }}</button>
      </div>
    </div>

    <div class="code mono">
      <div v-for="(l, i) in view.code" :key="i" :class="{ hl: view.hl.includes(i) }">{{ l || ' ' }}</div>
    </div>

    <div class="infer">
      <template v-for="(s, i) in view.steps" :key="i">
        <span v-if="i > 0" class="arr">→</span>
        <span class="step">{{ s }}</span>
      </template>
    </div>

    <div class="out-box">
      <div class="out-label">a &lt; b 的编译产物（示意）</div>
      <div class="out mono">
        <div v-for="(l, i) in view.out" :key="i">{{ l || ' ' }}</div>
      </div>
    </div>

    <div class="foot">{{ view.note }}</div>
  </div>
</template>

<style scoped>
.dict {
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #1f2328;
}
.mono {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, 'Cascadia Mono', monospace;
}
.controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
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
  font-size: 0.84rem;
  line-height: 1.8;
  white-space: pre;
  overflow-x: auto;
}
.code .hl {
  background: #eef4ff;
  border-radius: 4px;
  margin: 0 -6px;
  padding: 0 6px;
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
.out-box {
  background: #f7f3ff;
  border: 1px solid #ddd0f5;
  border-radius: 9px;
  padding: 10px 14px;
}
.out-label {
  font-size: 0.75rem;
  color: #7c3aed;
  margin-bottom: 6px;
}
.out {
  font-size: 0.82rem;
  line-height: 1.8;
  white-space: pre;
  overflow-x: auto;
  color: #3b3468;
}
.foot {
  margin-top: 10px;
  font-size: 0.83rem;
  color: #8a9099;
  line-height: 1.7;
}
</style>
