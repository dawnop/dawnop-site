<!-- viz-name: Dawn · 编译管线（一个函数走全程） -->
<script setup>
import { ref, computed } from 'vue'

const stages = {
  src: {
    label: '源码',
    sub: '.dawn',
    caption: '贯穿全程的例子：shapes.dawn 里的 area。下面每一步展示的都是它的真实形态。',
    art: `fn area(s: Shape) -> Float =
  match s {
    Circle(r)  -> 3.14159 * r * r
    Rect(w, h) -> w * h
    Point      -> 0.0
  }`,
  },
  lex: {
    label: '词法',
    sub: 'Lexer',
    caption: '手写的词法器，换行是显式 token（Dawn 靠换行断句，没有分号）。字符串插值在这一层就切成段。',
    art: `FN  IDENT(area)  LPAREN  IDENT(s)  COLON  TYPEIDENT(Shape)  RPAREN
ARROW  TYPEIDENT(Float)  EQ  NEWLINE
MATCH  IDENT(s)  LBRACE  NEWLINE
TYPEIDENT(Circle)  LPAREN  IDENT(r)  RPAREN  ARROW
FLOAT(3.14159)  STAR  IDENT(r)  STAR  IDENT(r)  NEWLINE
…`,
  },
  parse: {
    label: '语法',
    sub: 'Parser → AST',
    caption: '手写递归下降。带错误恢复：某处残缺时跳到安全点继续，一次报出整个文件的错误（LSP 也吃这套）。',
    art: `FnDecl area(s: Shape) -> Float
└─ Match (scrutinee: VarRef s)
   ├─ Arm  CtorPat Circle(r)   →  3.14159 * r * r
   ├─ Arm  CtorPat Rect(w, h)  →  w * h
   └─ Arm  CtorPat Point       →  Lit 0.0`,
  },
  check: {
    label: '检查',
    sub: '类型 + 效果',
    caption: '类型、效果、穷尽性都在这一层。检查结果直接标注回 AST，没有中间 IR。',
    art: `area : fn(Shape) -> Float        // 纯函数：签名无 !，正文也没碰 IO
match 穷尽 ✓                     // usefulness 判定：Circle/Rect/Point 全覆盖
每个分支的类型 = Float ✓`,
  },
  gen: {
    label: '字节码',
    sub: 'ASM 直出',
    caption: '真实产物（javap -c 节选）：Circle/Rect 走 instanceof + getfield，无参构造器 Point 是单例，比同一性就行。',
    art: `public static double area(Shape);
   0: aload_0
   3: instanceof    #8      // Shape$Circle
   6: ifeq          27
  10: checkcast     #8
  13: getfield      #12     // Shape$Circle.r:D
  17: ldc2_w        #13     // 3.14159
  21: dmul
  23: dmul
  24: goto          83
  27: instanceof    #16     // Shape$Rect
       …
  61: getstatic     #28     // Shape$Point.INSTANCE
  64: if_acmpne     73
  67: ldc2_w        #29     // 0.0
  73: new           #32     // dawn/rt/PanicError（unreachable）`,
  },
  jvm: {
    label: 'dawn run',
    sub: 'JVM',
    caption: '开发时的路径：字节码进内存 ClassLoader 直接跑，秒级反馈。dawn build 则打成可执行 jar。',
    art: `$ dawn run examples/shapes.dawn
circle with r = 1.0
rect 2.0 x 3.0
a point
a huge circle
total area: 125672.74158999999`,
  },
  native: {
    label: '--native',
    sub: 'native-image',
    caption: '同一份字节码喂给 GraalVM native-image，产出独立二进制。语言设计上没有反射 / eval / 动态加载，所以不需要任何配置文件。',
    art: `$ dawn build examples/shapes.dawn --native -o shapes
$ ./shapes          # 独立二进制，启动 ~7ms，无需 JVM
circle with r = 1.0
rect 2.0 x 3.0
a point
a huge circle
total area: 125672.74158999999`,
  },
}

const order = ['src', 'lex', 'parse', 'check', 'gen']
const outs = ['jvm', 'native']
const sel = ref('src')
const cur = computed(() => stages[sel.value])
</script>

<template>
  <div class="pipe">
    <div class="flow">
      <template v-for="(k, i) in order" :key="k">
        <button class="chip" :class="{ on: sel === k }" @click="sel = k">
          <span class="c-label">{{ stages[k].label }}</span>
          <span class="c-sub">{{ stages[k].sub }}</span>
        </button>
        <span v-if="i < order.length - 1" class="arr">→</span>
      </template>
      <span class="arr fork">→</span>
      <div class="outs">
        <button v-for="k in outs" :key="k" class="chip out" :class="{ on: sel === k }" @click="sel = k">
          <span class="c-label">{{ stages[k].label }}</span>
          <span class="c-sub">{{ stages[k].sub }}</span>
        </button>
      </div>
    </div>

    <pre class="art">{{ cur.art }}</pre>
    <div class="caption">{{ cur.caption }}</div>
  </div>
</template>

<style scoped>
.pipe {
  font-family: -apple-system, 'PingFang SC', sans-serif;
  color: #1f2328;
}
.flow {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}
.chip {
  font: inherit;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  padding: 6px 12px;
  border: 1px solid #d0d7de;
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
  transition: all 0.15s;
}
.chip:hover {
  border-color: #91b6f5;
}
.chip.on {
  border-color: #1677ff;
  background: #eef4ff;
}
.chip.out {
  border-style: dashed;
}
.chip.out.on {
  border-style: solid;
}
.c-label {
  font-size: 0.84rem;
  font-weight: 600;
  color: #3c4149;
}
.chip.on .c-label {
  color: #1677ff;
}
.c-sub {
  font-size: 0.7rem;
  color: #8a9099;
}
.arr {
  color: #b0b6bd;
  flex: none;
}
.outs {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.art {
  margin: 0;
  background: #f6f8fa;
  border: 1px solid #e4e8ec;
  border-radius: 9px;
  padding: 12px 14px;
  font-family: ui-monospace, 'SF Mono', Menlo, monospace;
  font-size: 0.8rem;
  line-height: 1.65;
  overflow-x: auto;
  min-height: 9.5em;
}
.caption {
  margin-top: 10px;
  font-size: 0.83rem;
  color: #8a9099;
  line-height: 1.7;
}
</style>
