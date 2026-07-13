---
title: Dawn 有 trait 了：把 typeclass 装进一门小语言
summary: Dawn 系列第二篇。上一篇结尾留了一句「泛型对 T 一无所知」，这一篇把账还上：单参数 typeclass，取 Rust 的一致性和孤儿规则、取 Haskell 的字典传递，六刀切完。中途还白捡一条定理：模块 DAG 加孤儿规则，跨模块的重复 impl 在结构上就不可能存在。两张交互图，一张玩孤儿规则，一张看字典在调用点怎么流。
tags: [编译器, PL, Kotlin, JVM, 类型系统]
---

上一篇写 Dawn 的时候，泛型一节的结尾其实埋了一句话：类型参数 `T` 是全然不透明的——不能比较、不能打印、不能调方法。当时的说法是「先用显式传函数顶着，疼了再加」。

三个里程碑之后，疼了。

疼点非常具体。用 Dawn 写 playground 后端和站点生成器时，每一个「按什么排序」的地方都得手工传比较函数；想写一个通用的 `max`，签名里就得多一个 `cmp` 参数，调用点每次都要重新想一遍「Int 怎么比来着」。而 `<` 这个运算符，只认 `Int`、`Float`、`String` 三张老面孔，自定义类型想排序，对不起，请写全套样板。这正是 typeclass 被发明出来要解决的事，于是 Dawn v1 的 trait 就只瞄准这一件事：**泛型约束 + 自定义排序**。

先看它长什么样，这段代码现在就能跑（文末 playground 里有同款示例）：

```rust
type Card = { rank: Int, name: String } derive Show, Ord

trait Describe[T] {
  fn describe(x: T) -> String
  fn loud(x: T) -> String = describe(x) ++ "!"   # 默认体，impl 可覆盖
}

impl Describe[Card] {
  fn describe(c: Card) -> String = "${c.name} (rank ${c.rank})"
}

fn best[T: Ord + Describe](xs: List[T]) -> String =
  match max(xs) {
    Some(x) -> x.loud()
    None -> "nothing"
  }

pub fn main() -> Unit !io = {
  let hand = [Card { rank: 3, name: "queen" }, Card { rank: 1, name: "pawn" }]
  println(to_string(hand[1] < hand[0]))   # true：derive Ord 按字段声明序比较
  println(best(sort(hand)))               # queen (rank 3)!
}
```

`derive Ord` 一句话，`<` 和 `sort` 就都认识 `Card` 了；`[T: Ord + Describe]` 是约束语法，trait 方法进普通函数命名空间，点号调用是白送的（Dawn 的 UFCS 本来就是语法糖）。

## 抄谁的作业，是个设计题

typeclass 这东西有两条成熟路线，各抄一半。

**语义抄 Rust**：名义式（impl 是显式声明，不是结构匹配）、全程序一致性（每个「trait × 类型」至多一个 impl）、孤儿规则（impl 只能写在 trait 或者主体类型的声明模块里）。这三条绑在一起买的是同一件事：**实例选择是全局唯一、与上下文无关的**。Haskell 的孤儿实例警告、Scala 隐式的「导入决定行为」，都是前车之鉴——Dawn 的 impl 全局生效，`use` 只影响名字可见性，永远不影响「用哪个实现」。

**实现抄 Haskell**：字典传递，不做单态化。这个选择几乎是后端替我做的。Dawn 的泛型本来就是擦除加装箱，`T` 在字节码里就是 `Object`，一个 trait 约束擦除之后自然就是「多传一个实现了该接口的对象」——字典传递跟这套后端严丝合缝。反过来，Rust 式单态化要给每个具体类型实例化一份代码，代码膨胀和 native-image 的封闭世界假设直接打架，还得推翻现有的泛型编译模型。一个人预算里，这不叫选择题。

还有一条克制：**运算符只放开比较族**。`< <= > >=` 桥接到预置的 `Ord` trait（唯一方法 `cmp(a, b) -> Int`，负零正表示小于等于大于），而 `==` 保持结构相等、不允许自定义——自定义相等会连坐 Map/Set 的键语义和模式匹配，风险大收益小。运算符重载的口子只开到「排序」为止。

## 白捡一条定理

写「跨模块重复 impl」的测试时，撞上一件有意思的事：这个测试**写不出来**。

推演一下。`impl Ord2[Point]` 合法的家只有两个：声明 `Ord2` 的模块，或者声明 `Point` 的模块。想造出重复，就得两个家各放一份。但 trait 模块里那份需要 `Point` 的名字（得 `use point`），类型模块里那份需要 `Ord2` 的名字（得 `use ord2`）——两个模块互相导入，成环了。而 Dawn 的模块依赖是严格的 DAG，环在加载期就是编译错误。至于第三方模块想插一脚？它两头都不沾，孤儿规则直接拦下，根本轮不到查重。

也就是说：**模块 DAG 加孤儿规则，跨模块的重复 impl 在结构上就不可能存在**。一致性检查真正要防的只剩同一个模块里写两份——一张哈希表的事。这条性质不是设计出来的，是两条各自独立成立的规则搭在一起送的。下面这张图可以亲手验证，把 impl 拖去三个模块试试，再试试「两处同时放」：

```viz
dawn-trait-orphan
```

## 字典在调用点怎么流

实现侧真正的主角是一个问题：`fn max2[T: Ord](a: T, b: T)` 的函数体里写 `a < b`，编译成什么？

答案分两种情况，检查器在每个调用点做「见证消解」（witness resolution），给约束找到出处：

- **具体类型**：`max2(p, q)` 里 `T = Point`，一致性保证 `Ord[Point]` 的 impl 全程序唯一，编译器直接查表拿到它——于是这个调用点**去虚化**，`cmp` 编译成一条 `invokestatic` 直呼 impl 的静态方法，没有任何动态分发。prelude 标量更狠：`cmp(1, 2)` 直接内联成 `LCMP` 指令，连调用都省了。
- **还是类型参数**：泛型函数调另一个泛型函数，`T` 依然刚性。这时调用者自己就带着一份隐藏的字典参数（每个「类型参数 × 约束」一个），原样转发下去；函数体里的 `a < b` 则对着字典做一次 `invokeinterface`。这是全系统里唯一残留的动态分发。

```viz
dawn-trait-dict
```

隐藏字典参数在实现里的形态，是我这次最满意的一个决定：**它就是一个合成的局部变量**（Symbol）。好处在 lambda 身上兑现——`fold(xs, first, fn(acc, x) => if acc < x { x } else { acc })`，lambda 体里的 `<` 需要外层函数的字典，而字典既然是局部变量，**现有的闭包捕获机制原样就把它捎进去了**，捕获分析、槽位分配、字节码生成，一行特判都没加。给「字典」发明一个专门的表示，然后到处开洞，是我一开始差点走上的路。

`derive Ord` 也顺着这条路：编译器给标了 derive 的类型生成一个字段字典序的 `cmp` 静态方法（和类型先比构造器声明序，同构造器再逐字段），挂上同一张 impl 表。对使用侧来说，derive 出来的和手写的没有任何区别——运算符、约束、`sort`、嵌套进别的 derive 字段，全部通用。

配套的标准库一并落地：`sort` / `max` / `min` 要求元素有 `Ord`，`sort_by` 收自定义比较函数，`max_by` / `min_by` 按键取极值（键要有 `Ord`）。排序是稳定的，极值平局取第一个——这两句话写进了 spec，不靠实现细节。

## 边界画在哪

v1 明确不做的，和做不了的，都值得写下来：

- **无条件 impl**：`impl Ord[List[T]] where T: Ord` 这种写不了，impl 主体只能是非泛型具名类型和四个标量。这是砍得最疼的一刀，但条件 impl 会把消解从查表变成递归搜索，报错质量和实现复杂度都翻档，v2 再说。
- **单类型参数、无关联类型、无 supertrait、无 dyn**：多参数 trait 和关联类型每加一个，类型推导和错误信息的复杂度都翻倍；dyn（存在类型）则动摇「具体调用点全去虚化」这个性能故事。
- **trait 方法不能当函数值传**：`let f = cmp` 报编译错误，提示包一层 lambda。字典是调用点的概念，脱离调用点的「裸方法」没有字典可查。
- **comptime 里不能用**：字典是运行时构造，编译期解释器 v1 拒绝一切带约束的调用（不带约束的 `sort_by` 可以用）。
- **`impl Ord[Bool]` 无处可写**：`Ord` 和 `Bool` 都住在 prelude，孤儿规则拦掉一切用户模块——Bool 保持不可排序。这是规则组合出的结果，但正好是想要的语义，就让它待着了。

## 账面

设计文档里预估六刀，实际也是六刀，每刀一个 commit：语法 → 注册与一致性 → 见证消解 → 字典代码生成 → stdlib 加 derive → 收尾（教程、spec、LSP、playground）。测试从 973 项涨到 1098 项，新增的 ~125 项里包括 22 个语法、35 个注册查重、27 个消解、11 个端到端运行、16 个 stdlib 与 derive；JVM 和 native-image 双跑，输出逐字一致——native 免配置的承诺在 trait 这关也没破，毕竟生成的字典接口和单例都是最朴素的类。

写完最大的感受是：**这个特性四分之三的工作量在检查器，四分之一在代码生成，语法几乎不要钱**。孤儿规则、一致性、约束消解、报错措辞（「no impl of `Ord` for `Point`」应该提示什么？「`T` 没有这个约束」又该提示什么？）才是 trait 的本体；字典传递反而是水到渠成的那一段。

语言的账也顺便结一下：Dawn 的「未来方向」清单里，trait 曾经排在第一位，现在移进了已完成。下一个大坑是 M6——用 Dawn 把这个博客的后端重写一遍，让它吃自己的狗粮。教程第 15 章和 [playground](https://dawn-lang.dawnop.com/playground.html) 的 traits 示例已经上线，完整的设计推演（包括每一条被否掉的路线）在仓库的 [trait.md](https://github.com/dawnop/dawn-lang/blob/main/docs/trait.md)。
