---
title: 【TopK 优化系列 0】总览
---

> **TopK 优化系列**：**0 总览（本篇）** · [1 Bitonic](/article/topk-bitonic-select) · [2 Radix](/article/topk-radix-select) · [3 二分阈值](/article/topk-binary-threshold)

本文面向具备 GPU 优化经验、但未系统接触过 TopK 算法的读者。不再解释 warp、shared memory、atomic、coalescing、occupancy 等基础概念，而是从这些硬件约束出发，推导 TopK 几类主流算法的设计动机，并辅以可交互图示建立直觉。每一流派的实现细节与工程优化，作为本系列后续三篇（1 Bitonic / 2 Radix / 3 二分阈值）展开。

## 1. selection，而非 sort

一种常见的默认实现是先排序再取前 $k$ 个：`argsort` 得到全序，再切片。它的问题在于：为获取前 $k$ 个，同时对其余 $n-k$ 个完成了排序，而后者的顺序与结果无关。

从复杂度看，"选出最大的 $k$ 个"是 **selection** 问题，下界为 $O(n)$——至少需检视每个元素一次，但无需达到 sort 的 $O(n\log n)$。这给出本文的出发点：

> 应尽量避免 sort；理想的 TopK 只需单遍扫描数据。

对 GPU，这一原则更具体：**理想的 TopK 是访存受限（memory-bound）的单遍扫描**。若实测吞吐显著低于显存带宽，通常意味着在做多余的工作。

但"单遍选出 top-k"在 GPU 上并不容易，难点恰好落在 GPU 优化最敏感的几处：**branch divergence、atomic 竞争、不规则访存、occupancy**。后文三类算法，本质上是针对不同参数区间、绕开不同硬件痛点的三种答案。决定选哪一种的，主要是三个量：$k$ 的量级（能否把候选结构放进片上）、单条长向量还是海量短向量（并行粒度）、需要精确还是可接受近似。

## 2. 为什么不能照搬 CPU 算法

CPU 上 TopK 有两个标准解，移植到 GPU 都不顺。

**最小堆（min-heap）**：维护容量为 $k$ 的 min-heap，遍历时大于堆顶则替换下沉，$O(n\log k)$。但 heap 是串行、依赖前序状态的结构，每次替换是一串有依赖的比较与交换。GPU 上成千上万线程要么争抢同一个 heap（atomic 噩梦），要么各维护局部 heap 再归并——heap 的串行性与 SIMT 的大规模并行天生不合。

**Quickselect**：用 partition 把数据按 pivot 分成两部分，只递归包含第 $k$ 个元素的一侧，期望 $O(n)$，是 `nth_element` 的内核。但它的划分方向数据相关——在 SIMT 下，一个 warp 内线程分流不一致就要两条路径都走一遍（branch divergence）；partition 的数据搬移也不规则，破坏 coalescing。

结论：GPU 需要的是**数据无关、访存规则、atomic 少**的算法。下面三类各自从一个硬件约束切入，先用一张表把它们并排放在一起，后文逐一展开：

| 维度 | Bitonic Top-K | Radix Select | 二分阈值 |
|---|---|---|---|
| 切入约束 | branch divergence | 片上容量（大 $k$） | 并行粒度（短向量） |
| 数据结构 | 容量 $k$ 的有序序列（sorting network） | $2^d$ histogram | 无，二分一个阈值 |
| 片上占用 | 随 $k$ 增长（register/shared） | 常数 $\sim1$ KB、与 $k$ 无关 | 行长 $R$ 的 register 数组 |
| 对 $k$ 的依赖 | $O(k\log^2 k)$，强 | 近似无关 | 无关（受 $R$） |
| 并行粒度 | warp/block 维护一段 top-k | grid 多趟、全数组 | 一 warp 一行 |
| 最佳区间 | 小 $k$（$\lesssim 256$） | 任意 / 大 $k$、median | 海量短行、各自小 $k$ |
| 主瓶颈 → 优化 | 喂数带宽 → vectorized load | atomic → hierarchical / write buffer | warp 计数 → ballot + popc |

## 3. 流派一 · Bitonic Top-K（数据无关，适合小 $k$）

**切入的约束：GPU 不利于数据相关的 branch。** 既然如此，就用一种比较位置完全固定、与数据无关的排序——**sorting network**，具体是 **bitonic sort**。

sorting network 由一组预先确定的 compare-exchange 构成：无论输入是什么，执行的比较步骤完全一致，没有数据相关的 branch。下图是 8 路 bitonic network，每一列是一组同时进行、方向固定的 compare-exchange；点"步进"逐列观察，换随机数据可验证 network 结构不随数据改变：

```viz
topk-bitonic
```

Bitonic Top-K 的做法：在片上维护一个容量为 $k$ 的有序序列，将输入分块，每块与该序列做 bitonic merge、只保留较大的 $k$ 个，扫完即得 top-k。整个过程在 register / shared memory 内完成，**无 atomic、无 branch divergence**，warp 内的 compare-exchange 直接用 shuffle 指令实现，这正是它在小 $k$ 下极快的原因。

**适用区间**：network 的 comparator 数量为 $O(k\log^2 k)$，且容量 $k$ 的有序序列要常驻 register/shared，$k$ 一大就放不下。深入篇按 A100 量化：高占用区间约 $k\lesssim 256$，寄存器硬上限约 $k\le 3680$（再大溢出 local memory）。

> 实现细节与完整 kernel（sorting network 构造、warp shuffle register 内排序、分块 merge、vectorized load 喂数）见本系列第 1 篇：[【TopK 优化系列 1】Bitonic](/article/topk-bitonic-select)。

## 4. 流派二 · Radix Select（histogram 定位，适合任意 $k$）

**切入的约束：大 $k$ 时片上放不下候选结构。** 那就不维护任何有序结构，改用 **histogram** 定位第 $k$ 大的元素，片上只需常数大小的计数器。

Radix Select 与 radix sort 同源，但只 select 不 sort：把数值看作比特串，从最高位起每次取 $d$ 个 bit（对应 $2^d$ 个 bin），统计 histogram，从高 bin 向下累加找到第 $k$ 大所在的 bin，只对该 bin 进入下一组 bit。下图演示这一逐轮缩桶：更高的 bin 整段进 top-k，pivot bin 含边界、只对它递归，其余排除，候选每轮缩约 $1/2^d$：

```viz
topk-radix
```

片上只需一个 $2^d$ 的 histogram（如 $d=8$ 即 256 个计数器、约 $1$ KB），**与 $k$ 无关**——成本对 $k$ 近似平坦，因此 $k$ 取到 $n/2$（median）乃至任意分位数都从容。总读量是几何级数 $\approx n$（首趟主导），算法带宽受限。

它的代价是 **atomic**：统计 histogram 时大量线程要往同一组计数器累加。这里就要引入第一项与该算法绑定的工程优化——**hierarchical atomics**：把 atomic 尽量留在便宜的层级，warp 内先用 shuffle/ballot reduction、block 内在 shared memory 聚出局部 histogram，最后才以常数次 global atomic 合并。下图对比朴素 global atomic 与三级聚合的冲突：

```viz
topk-atomics
```

围绕 radix 还有一系列工程优化：**write buffer**（筛出的元素攒够一批再写 global）、**task rescheduling**（批量场景横向对齐迭代以保 occupancy）、**adaptive scaling**（高位 bit 聚集时减去随机元素打散分布）、**vectorized load**。这些都在深入篇展开。

> 逐位 select 的完整 kernel、hierarchical atomics / write buffer / task rescheduling / adaptive scaling 的实现，见本系列第 2 篇：[【TopK 优化系列 2】Radix](/article/topk-radix-select)。

## 5. 流派三 · 二分阈值（一 warp 一条，适合海量短向量）

**切入的约束：并行粒度。** 当输入是海量短向量、每条各自求 top-k（每条长度常小于 1024），把一条拆给多个 block 并不划算，于是**让一个 warp 处理一条向量**，32 个线程协作。

在 warp 内求 top-k 的高效途径，不是定位具体的第 $k$ 大元素，而是**二分一个阈值（threshold）** $\tau$，使"不小于 $\tau$ 的元素恰好 $k$ 个"。下图展示 threshold 在数轴上的逼近：

```viz
topk-threshold
```

关键在于计数这一步：warp 内用 `__ballot_sync` + `__popc`（投票 + 数 1 的个数）一条指令数出有多少元素过线，**无 branch**；上下界用 shuffle reduction 求得。整个过程几乎不写中间结果、register 占用极低。

进一步，**early-stop 近似**把二分固定为若干轮提前停手，接受一个近似的 $k$——在容忍误差的场景下用很小的精度换数倍速度，且所有 warp 轮数一致、无尾部发散。这一路的成本与 $k$ 无关，受限的是**行长 $R$**（$R/32$ 的 register 数组撑占寄存器），最佳区间是 $R\lesssim 1024$、batch 极多的 row-wise 场景。

> warp 内 ballot/popcount 计数的完整实现、early-stop 与精度权衡，见本系列第 3 篇：[【TopK 优化系列 3】二分阈值](/article/topk-binary-threshold)。

## 6. 自适应选型：消除 performance cliff

没有单一算法在 $k$ 的全区间上都最优：小 $k$ 时 bitonic 优于 radix（后者 histogram 的固定开销偏高），大 $k$ 时 bitonic 放不下片上、只能 radix。固定用一个算法，跨越某个 $k$ 就会撞上性能骤降（performance cliff）。下图把这件事画出来——固定 $n$，Bitonic 的成本随 $k$ 凸增、到寄存器墙后不可行，Radix 对 $k$ 近似平坦；取两者**下包络**（更低即更快）即逐点最优。拖动 $n$ 滑杆，交叉点 $k^*$ 随之右移：

```viz
topk-selection-map
```

生产级实现按 $k$ 动态选择：小 $k$（如 $\le128$）走 bitonic、在 register 内完成并避开 shared memory atomic；大 $k$ 走 radix、复杂度与 $k$ 无关。切换阈值随数据规模 $n$ 由经验公式确定，例如

$$\text{Factor}(n) = \frac{1}{3} + \frac{1.6}{\log_2 n - 9.5}$$

在 $n=8192$ 时约 $k\approx195$、$n=131072$ 时约 $k\approx878$。如此在整个 $k$ 区间贴近逐点最优，自动规避 cliff。

## 7. 结语

把三类算法放在一起看，它们其实是同一种思路的三次应用：**先确定当前的硬件约束，再据此选择数据结构**。

- 约束是 branch divergence → 用数据无关的 sorting network（bitonic，小 $k$）；
- 约束是片上容量 → 用与 $k$ 无关的 histogram 定位（radix，任意 $k$）；
- 约束是并行粒度 → 一 warp 一条 + threshold 二分（短向量批量）。

而工程优化并不独立于算法，它依附于具体算法的瓶颈：radix 的瓶颈是 atomic，于是有 hierarchical atomics 与 write buffer；threshold 法的瓶颈是 warp 内计数，于是有 ballot/popcount；bitonic 的优势本就在 register 内无 atomic，工程重点转向喂数的带宽。换言之，**算法决定了瓶颈在哪里，工程优化决定了把这个瓶颈压到多低**。三篇深入文章会沿着各自的瓶颈，给出可落地的 CUDA 实现，并在 A100 上把各自的边界量化到寄存器 / occupancy / 带宽，与上节的选型图相互印证。
