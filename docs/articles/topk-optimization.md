---
title: 【TopK 优化系列 0】总览
summary: GPU 上求 top-k 到底能跑多快？有一回 profile 发现它是瓶颈，我才认真想了这个问题。这个系列记录我把它想清楚的过程，这一篇是导言：先讲清为什么 top-k 是 selection 而非 sort、为什么 CPU 上顺手的 heap 和 quickselect 搬到 GPU 都不顺，再把三类主流算法——Bitonic、Radix、二分阈值——按各自切入的硬件约束摆开，最后聊聊怎么按 k 自适应选型、绕开 performance cliff。
---

> **TopK 优化系列**：**0 总览（本篇）** · [1 Bitonic](/article/topk-bitonic-select) · [2 Radix](/article/topk-radix-select) · [3 二分阈值](/article/topk-binary-threshold)

做 GPU kernel 的时候，我隔三差五就撞见 top-k：推荐里取 score 最高的若干个、采样里筛 logits、稀疏化里留最大的几个权重……每次我都顺手调个库就过去了。直到有一回 profile 发现它居然是瓶颈，我才停下来认真想了一个问题：GPU 上求 top-k，到底能多快？

这个系列想记录的不只是结论，而是我把这件事想清楚的过程。这一篇是导言，先把问题摆正、再把三类主流算法的「为什么」讲明白；具体的 kernel 和工程优化，留给后面三篇（[1 Bitonic](/article/topk-bitonic-select) / [2 Radix](/article/topk-radix-select) / [3 二分阈值](/article/topk-binary-threshold)）。默认你有 GPU 优化经验——warp、shared memory、atomic、coalescing、occupancy 这些我就不展开了。

## 1. selection，而非 sort

我最初的实现就是「排序再切片」：`argsort` 拿到全序，取前 $k$ 个。能跑，但一想就觉得亏——为了那前 $k$ 个，我顺带把另外 $n-k$ 个也排好了，而它们的内部顺序跟我要的结果半点关系都没有。

复杂度也是这么说的：「选出最大的 $k$ 个」是 **selection** 问题，下界只有 $O(n)$——每个元素至少看一眼——根本用不到 sort 的 $O(n\log n)$。于是我给自己定了个出发点：

> 别排序；理想的 top-k 只扫一遍数据。

放到 GPU 上，这句话可以更具体：**理想的 top-k 应该是一次访存受限（memory-bound）的单遍扫描**。如果我实测的吞吐明显低于显存带宽，那基本就是在做多余的活。

可惜「单遍选出 top-k」在 GPU 上并不好做，难点恰好全压在 GPU 最敏感的几个点上：**branch divergence、atomic 竞争、不规则访存、occupancy**。后面三类算法，本质上就是针对不同参数、绕开不同痛点的三种答卷。挑哪一种，我后来发现主要看三个量：$k$ 的量级（候选结构塞不塞得进片上）、是单条长向量还是海量短向量（并行粒度）、要精确还是能接受近似。

## 2. 为什么不能照搬 CPU 算法

我第一反应是把 CPU 上顺手的两个解搬过来，结果都不顺。

**最小堆（min-heap）**：维护容量 $k$ 的 min-heap，比堆顶大就替换下沉，$O(n\log k)$。但 heap 是串行的，每一步都依赖前一步的状态，一次替换就是一串有依赖的比较和交换。GPU 上几千个线程，要么抢同一个 heap（atomic 噩梦），要么各搞各的局部 heap 再归并——heap 的串行性和 SIMT 的大规模并行，天生合不来。

**Quickselect**：partition 按 pivot 把数据分两半，只递归含第 $k$ 个的那半，期望 $O(n)$，`nth_element` 就是它。但它的划分方向是数据相关的——SIMT 下一个 warp 里线程分流不一致，两条路就都得走一遍（branch divergence）；partition 搬数据也不规则，coalescing 直接没了。

折腾下来结论很清楚：GPU 要的是**数据无关、访存规则、atomic 少**的算法。下面这三类，各从一个硬件约束切入。我先用一张表把它们并排放一起，后面再逐个展开：

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

先说我最喜欢的一个——因为它把「GPU 不喜欢数据相关 branch」这件事用得最彻底。

**切入的约束：GPU 不利于数据相关的 branch。** 既然如此，那就干脆用一种比较位置完全固定、跟数据无关的排序——**sorting network**，具体是 **bitonic sort**。它由一组预先排好的 compare-exchange 组成：无论输入是什么，走的比较步骤一模一样，没有一处数据相关的 branch。下面这张图是 8 路 bitonic network，每一列是一组同时进行、方向固定的 compare-exchange；点「步进」逐列看，换随机数据你会发现 network 结构压根不随数据变：

```viz
topk-bitonic
```

求 top-k 的话不必全排：在片上维护一个容量 $k$ 的有序序列，把输入分块，每块跟它做一次 bitonic merge、只留较大的 $k$ 个，扫完就是 top-k。整个过程都在 register / shared memory 里，**没有 atomic、没有 branch divergence**，warp 内的 compare-exchange 直接用 shuffle 指令做——这就是它在小 $k$ 下快得离谱的原因。

代价也很实在：comparator 数量是 $O(k\log^2 k)$，而且那个容量 $k$ 的有序序列得常驻 register/shared，$k$ 一大就塞不下了。我在第 1 篇按 A100 算过：高占用区间大概 $k\lesssim 256$，硬上限约 $k\le 3680$，再大就溢出到 local memory、优势全失。

> 实现细节与完整 kernel（sorting network 构造、warp shuffle 在 register 内排序、分块 merge、vectorized load 喂数）见本系列第 1 篇：[【TopK 优化系列 1】Bitonic](/article/topk-bitonic-select)。

## 4. 流派二 · Radix Select（histogram 定位，适合任意 $k$）

那 $k$ 很大、片上放不下有序结构怎么办？换个思路：**干脆不维护任何有序结构**，改用 histogram 去定位第 $k$ 大的元素，片上只要常数大小的计数器。

Radix Select 跟 radix sort 同源，但只 select 不 sort：把数值当比特串，从最高位起每次取 $d$ 个 bit（$2^d$ 个 bin），统计 histogram，从高 bin 往下累加找到第 $k$ 大落在哪个 bin，只对那个 bin 继续往下一组 bit。下图就是这个「逐轮缩桶」：更高的 bin 整段进 top-k，pivot bin 含边界、只对它递归，其余排除，候选每轮缩到约 $1/2^d$：

```viz
topk-radix
```

片上只要一个 $2^d$ 的 histogram（$d=8$ 就是 256 个计数器、约 $1$ KB），**和 $k$ 完全无关**——成本对 $k$ 近似平坦，所以哪怕 $k$ 取到 $n/2$（求中位数）甚至任意分位数都不慌。总读量是个几何级数 $\approx n$（首趟主导），整体带宽受限。

它的命门是 **atomic**：统计 histogram 时一堆线程往同一组计数器上撞。这里就得请出第一项跟它绑定的工程优化——**hierarchical atomics**：把 atomic 尽量留在便宜的层级，warp 内先 shuffle/ballot 归约、block 内在 shared memory 聚出局部 histogram，最后才用常数次 global atomic 合并。下图对比朴素 global atomic 和三级聚合的冲突，差距挺直观：

```viz
topk-atomics
```

围着 radix 还有一串工程优化：**write buffer**（筛出的元素攒一批再写 global）、**task rescheduling**（批量场景横向对齐迭代、保住 occupancy）、**adaptive scaling**（高位 bit 扎堆时减个随机元素把分布打散）、**vectorized load**。这些我都放第 2 篇细讲。

> 逐位 select 的完整 kernel、hierarchical atomics / write buffer / task rescheduling / adaptive scaling 的实现，见本系列第 2 篇：[【TopK 优化系列 2】Radix](/article/topk-radix-select)。

## 5. 流派三 · 二分阈值（一 warp 一条，适合海量短向量）

前两种都假设「一个大数组」。但我后来遇到的场景常常是另一种：海量短向量，每条各自求 top-k（每条往往不到 1024）。这时候把一条拆给好几个 block 太亏了，于是换个粒度——**一个 warp 干一条**，32 个线程协作。

在 warp 内求 top-k，我发现最划算的不是去定位那个第 $k$ 大元素，而是**二分一个阈值（threshold）** $\tau$，让「不小于 $\tau$ 的元素正好 $k$ 个」。下图就是 $\tau$ 在数轴上一步步逼近的样子：

```viz
topk-threshold
```

妙的是计数这一步：warp 内用 `__ballot_sync` + `__popc`（投票 + 数 1）一条指令就数出有多少元素过线，**没有 branch**；上下界用 shuffle 归约求。全程几乎不写中间结果，register 占用极低。

更进一步，**early-stop 近似**把二分固定成若干轮就提前收手，接受一个近似的 $k$——在能容忍误差的场景（比如训练里的稀疏化）拿很小的精度换几倍速度，而且所有 warp 轮数一致、没有尾部发散。这一路的成本跟 $k$ 无关，真正卡它的是**行长 $R$**（$R/32$ 的 register 数组会吃寄存器），所以它的主场是「$R$ 不大、batch 极多」的 row-wise。

> warp 内 ballot/popcount 计数的完整实现、early-stop 与精度权衡，见本系列第 3 篇：[【TopK 优化系列 3】二分阈值](/article/topk-binary-threshold)。

## 6. 自适应选型：消除 performance cliff

三种都讲完，问题来了：到底用哪个？我的答案是——别只用一个。没有哪种算法在整个 $k$ 区间都最优：小 $k$ 时 bitonic 赢 radix（后者 histogram 的固定开销偏高），大 $k$ 时 bitonic 压根放不下、只能 radix。要是死守一个，跨过某个 $k$ 就会一头撞上性能骤降（performance cliff）。

下图把这件事画出来了——固定 $n$，Bitonic 的成本随 $k$ 凸增、到寄存器墙后直接不可行，Radix 对 $k$ 近似平坦；两条线取**下包络**（更低就是更快）就是逐点最优。拖一下 $n$ 滑杆，交叉点 $k^*$ 会跟着右移：

```viz
topk-selection-map
```

所以生产级的实现都按 $k$ 动态挑：小 $k$（比如 $\le128$）走 bitonic，全程 register、躲开 shared memory atomic；大 $k$ 走 radix，复杂度与 $k$ 无关。切换阈值还得随数据规模 $n$ 调，有人用这样的经验公式：

$$\text{Factor}(n) = \frac{1}{3} + \frac{1.6}{\log_2 n - 9.5}$$

代进去，$n=8192$ 时约 $k\approx195$、$n=131072$ 时约 $k\approx878$。这样在整个 $k$ 区间都贴着逐点最优走，cliff 自然就没了。

## 7. 写在最后

把三种算法摆一起看，它们其实是同一个套路用了三遍：**先认清当前的硬件约束，再据此挑数据结构**。

- 约束是 branch divergence → 用数据无关的 sorting network（bitonic，小 $k$）；
- 约束是片上容量 → 用与 $k$ 无关的 histogram 定位（radix，任意 $k$）；
- 约束是并行粒度 → 一 warp 一条 + 阈值二分（短向量批量）。

而且我越做越觉得，工程优化从来不是独立于算法的——它就长在每个算法各自的瓶颈上：radix 的瓶颈是 atomic，于是有 hierarchical atomics 和 write buffer；阈值法的瓶颈是 warp 内计数，于是有 ballot/popcount；bitonic 本来就在 register 里没 atomic，于是重点全转到喂数带宽。一句话概括：**算法决定瓶颈在哪，工程优化决定能把它压多低**。接下来三篇，我会顺着各自的瓶颈给出能跑的 CUDA，并在 A100 上把边界量化到寄存器 / occupancy / 带宽，跟上面那张选型图相互印证。下一篇见。
