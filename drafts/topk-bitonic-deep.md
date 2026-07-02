---
title: 【TopK 优化系列 1】Bitonic
summary: GPU 最怕数据相关的 branch，而 bitonic 把这点用到了极致——比较位置完全固定。这一篇我从 warp 内 32 元素的 bitonic sort 写起，一步步拼出能跑的 warp→block→grid 三级 kernel，再聊怎么把数据喂快，最后在 A100 上算清楚 k 到底能多大、卡在哪。
---

> **TopK 优化系列**：[0 总览](/article/topk-optimization) · **1 Bitonic（本篇）** · [2 Radix](/article/topk-radix-select) · [3 二分阈值](/article/topk-binary-threshold)

这是系列第 1 篇。先说我为什么从它开头：导言里提过，GPU 最怕数据相关的 branch，而 bitonic 把这点用到了极致——它的比较位置完全固定、跟数据无关。这篇我从一个 warp 内 32 个元素的 bitonic sort 写起，一步步拼出能跑的 warp → block → grid 三级 kernel，再聊聊怎么把数据喂快，最后在 A100 上算清楚 $k$ 到底能多大。

```viz
topk-bitonic
```

## 1. bitonic 序列与 bitonic sort

先把原语复习一下。一个 **bitonic 序列**是先单调增、后单调减（或循环移位）的序列。bitonic sort 靠的是这么一条性质：对长度 $2m$ 的 bitonic 序列，把前后两半逐位做 compare-exchange（第 $i$ 位与第 $i+m$ 位比较，小的放前），得到的两半各自仍是 bitonic 序列，且前半所有元素不大于后半。递归下去就排好了，而整个过程的比较位置只跟下标有关、跟数据无关——这正是我要的。

对长度 $n=2^p$ 的输入，bitonic sort 由 $\tfrac{p(p+1)}{2}$ 列 comparator 构成，总比较次数 $O(n\log^2 n)$，比最优 sort 贵。但它**一处数据相关的 branch 都没有**，这对 SIMT 是决定性的——贵一点的比较次数，换来零 divergence，我觉得很划算。

## 2. warp 内 32 元素 bitonic sort

怎么落到硬件上？最自然的映射：一个 warp 的 32 个 lane 各持有一个值，lane 间的 compare-exchange 用 `__shfl_xor_sync` 完成——partner 的 lane 号正是 `laneid ^ j`。先用一行数字把过程看清楚（取 8 个方便观察，规律与 32 个一致）：每一阶段比较的下标对（弧线）由 `i XOR j` 固定决定，点"步进"逐列看比较与交换：

```viz
bitonic-row
```

对应的代码，每个 lane 持一个值、阶段 `(k, j)` 双重循环：

```cpp
// 32 元素 bitonic sort：每个 lane 持一个值，返回后按 lane 号升序排列。
// 阶段 (k, j)：与 lane^j 比较；(lane & k)==0 的段升序，否则降序。
__device__ __forceinline__ float warp_bitonic_sort(float v) {
    const unsigned FULL = 0xffffffffu;
    const int lane = threadIdx.x & 31;
    for (int k = 2; k <= 32; k <<= 1) {
        for (int j = k >> 1; j > 0; j >>= 1) {
            float partner = __shfl_xor_sync(FULL, v, j);
            bool ascending = ((lane & k) == 0);   // 本段是否升序
            bool iAmLower  = ((lane & j) == 0);    // 在交换对中本 lane 是否为低位侧
            float lo = fminf(v, partner);
            float hi = fmaxf(v, partner);
            // 升序段：低位 lane 取小、高位 lane 取大；降序段相反
            v = (ascending == iAmLower) ? lo : hi;
        }
    }
    return v;
}
```

我想强调的是：整个函数**没有一个数据相关的 `if` 去改控制流**。`ascending`、`iAmLower` 只跟 lane 号有关，`fminf/fmaxf` 编译成无 branch 的 `min/max` 指令，三元运算编译成 `selp`。把内层一次 compare-exchange 反汇编看看（`nvcc -arch=sm_80 -ptx`），关键指令如下：

```ptx
; float partner = __shfl_xor_sync(0xffffffff, v, j);
shfl.sync.bfly.b32  %f2, %f1, %r_j, 0x1f, 0xffffffff;
; float lo = fminf(v, partner);  float hi = fmaxf(v, partner);
min.f32             %f3, %f1, %f2;
max.f32             %f4, %f1, %f2;
; v = (ascending == iAmLower) ? lo : hi;
setp.eq.s32         %p1, %r_asc, %r_low;   // 生成谓词，不跳转
selp.f32            %f1, %f3, %f4, %p1;    // 谓词选择，无 branch
```

整段连一个 `bra`（跳转）都没有：lane 间交换是 `shfl.sync.bfly`，取大小是单条 `min/max`，方向选择交给 `setp` 生谓词、`selp` 直接选值。于是一个 warp 走的指令序列完全一致，divergence 是零——这就是我说「用到极致」的意思。

想要最大的若干个，排成升序后末尾就是最大；想让 top-k 在前，把最后一列方向取反、或直接读高 lane 即可。

**那两层循环其实是分治被拍平的结果。** 第一次读 `(k, j)` 双重循环我总觉得不直观——它到底在分治什么、为什么这样取方向，得在脑子里把循环重新折叠回递归才看得清。所以这里补一份等价的递归版（CPU、按下标寻址），它读起来就是 bitonic sort 的定义本身：

```cpp
// 把 a[lo .. lo+cnt-1] 排成 dir 方向（升序 dir=true）。整段升序排序即 bitonicSort(a, 0, n, true)。
void bitonicSort(float* a, int lo, int cnt, bool dir) {
    if (cnt <= 1) return;
    int m = cnt / 2;
    bitonicSort(a, lo,     m, true);   // 前半排成升序
    bitonicSort(a, lo + m, m, false);  // 后半排成降序 → 前升后降，整段成 bitonic
    bitonicMerge(a, lo, cnt, dir);     // 再把这个 bitonic 段 merge 成 dir 序
}

// 前提：a[lo .. lo+cnt-1] 已是 bitonic；把它 merge 成 dir 序。
void bitonicMerge(float* a, int lo, int cnt, bool dir) {
    if (cnt <= 1) return;
    int m = cnt / 2;
    for (int i = lo; i < lo + m; i++)      // half-cleaner：i 与 i+m 比较
        if ((a[i] > a[i + m]) == dir) std::swap(a[i], a[i + m]);
    bitonicMerge(a, lo,     m, dir);       // 劈成的两半各自仍是 bitonic，递归 merge
    bitonicMerge(a, lo + m, m, dir);
}
```

正确性顺着这两个函数读一遍就出来了，分三层：①`bitonicMerge` 里那个 for 循环是一次 **half-cleaner**——对一个长 `2m` 的 bitonic 段，把 `i` 与 `i+m` 比较、小的留前半，结果两半各自仍是 bitonic 且「前半全部 ≤ 后半全部」；②于是对两半递归 `bitonicMerge` 各自排好，拼起来整段有序——这就证明了 `bitonicMerge` 把任意 bitonic 段排成了有序；③`bitonicSort` 把前半排升、后半排降，「升 ++ 降」正好是一个 bitonic 段，再交给已证正确的 `bitonicMerge`——对 `cnt` 做归纳（`cnt=1` 平凡）即得整体正确。

递归版与上面的 warp 版对 2 的幂长度产生**逐个相同**的比较序列，只是寻址方式不同：递归的「分哪半」靠调用栈、「什么方向」靠 `dir` 参数显式传；warp 版把它们全编码进 lane 号的位运算——`lane ^ j` 代替「算左右半下标」，`(lane & k) == 0` 代替「dir 一路传下来」。理解和证明看递归版，真上 GPU 跑则要循环展开版：SIMT 下一个 warp 的 32 个 lane 必须锁步执行同一条指令，没有 per-element 的调用栈可递归，而同一深度的那些递归调用本就独立同构、会塌缩成「所有 lane 并行做一拍 `__shfl_xor_sync`」——这正是循环里的一个 `(k, j)` 阶段。

## 3. 从 sort 到 Top-K：分块 merge

但全 sort 还是浪费——我要的只是前 $k$ 个。更聪明的办法是**维护一个容量 $k$ 的有序序列**，不断把新数据并进来、只留最大的 $k$ 个。这又用到 bitonic 的另一条性质：

> 一个**降序**序列接上一个**升序**序列，整体是一个 bitonic 序列；对它做一遍 bitonic merge，即可重新排好序。

于是流程是这样：把当前 top-k（降序）与 $k$ 个新候选（升序）逐位取较大者，得到的「较大半」恰好是个 bitonic 序列，再对它做一遍 bitonic merge 就排回降序——前 $k$ 个就是新的 top-k。下图拿 $k=8$ 演示（柱高即数值，规律与 $k=32$ 一致）：

```viz
topk-merge
```

动手写 `merge_top32` 之前，有两个我自己一开始也含糊的点得先厘清。

**这 $k$ 个候选是哪来的？** 不是另开一块预先排好的数组，而是**从输入流里顺次截的**：每个 warp 顺序扫过自己负责的那段输入，每凑满连续 32 个元素，就把这一批当作一组「候选」并入当前 top-32。换句话说，候选就是刚从显存读进来的**原始、没排序**的值，每读 32 个触发一次 merge，循环吃完整条切片。

**为什么候选要先排成升序？** 因为 bitonic merge 的前提是输入本身已经是个 bitonic 序列。我把当前 top-32 **一直维持降序**，那只有新候选排成**升序**时，「降序 ++ 升序」接起来才是个先降后升的 bitonic（就是上面那条性质），随后一遍 merge 才会正确收敛。所以 `merge_top32` 第一步，就是用 §2 的 `warp_bitonic_sort` 把这 32 个原始候选排成升序——数据无关、开销固定。要是候选保持乱序，拼出来的不是 bitonic，单遍 merge 排不好。

拿 $k=32$、一个 warp 维护 top-32 举例：

```cpp
// 维护 top-32：running 为当前 top-32（降序，按 lane 号 0..31 递减）。
// cand 为本轮 32 个新候选。合并后 running 更新为新的 top-32。
__device__ __forceinline__ float merge_top32(float running_desc, float cand) {
    const unsigned FULL = 0xffffffffu;
    const int lane = threadIdx.x & 31;
    // 候选先升序排好
    float cand_asc = warp_bitonic_sort(cand);
    // running(降序) ++ cand(升序) 构成 bitonic；逐位取较大者，得到长度 32 的较大半
    float merged = fmaxf(running_desc, cand_asc);
    // merged 现在是一个 bitonic 序列，再做一遍 bitonic merge 排成降序
    for (int j = 16; j > 0; j >>= 1) {
        float partner = __shfl_xor_sync(FULL, merged, j);
        bool iAmLower = ((lane & j) == 0);
        // 降序：低位 lane 取大、高位 lane 取小
        merged = iAmLower ? fmaxf(merged, partner) : fminf(merged, partner);
    }
    return merged;
}
```

`running` 初始是 $-\infty$（空集的 top-32），之后每读 32 个候选就 `merge_top32` 并一次；全程只有 register 里的 `shfl`/`min`/`max`，不落任何中间结果。$k>32$ 就把「一个 lane 一个值」换成「一个 lane 持有 $k/32$ 个值的 register 数组」，compare-exchange 在数组维度展开即可，思路不变。把这块单 warp 的逻辑塞进完整的 warp → block → grid 流程，就是下一节。

## 4. 完整算法：warp → block → grid 三级归约

现在把前面的零件拼成一个能跑的 kernel。整体是个**三级归约**：每个 warp 在 register 里独立维护一段 top-32 → block 内把各 warp 的 top-32 并成 block 的 top-32 → 各 block 的结果在 global 再并成最终 top-32。三级都复用同一个 `merge_top32`。

**第一级：warp 各扫各的切片。** 输入按 warp 切开，每个 warp 用一个 warp-stride 循环顺序吃自己的元素，维护一段 register 里的 top-32：

```cpp
#include <math_constants.h>   // CUDART_INF_F
constexpr int WARP = 32;

// 每个 warp 维护 register 内的 top-32（降序）；输出本 block 的 top-32 到 block_out。
// 启动配置：blockDim.x = warpsPB*32，shared 大小 = warpsPB*32*sizeof(float)。
__global__ void topk32_kernel(const float* __restrict__ input, int n,
                              float* __restrict__ block_out) {
    const int lane    = threadIdx.x & 31;
    const int warpId  = threadIdx.x >> 5;
    const int warpsPB = blockDim.x >> 5;
    const int globalW = blockIdx.x * warpsPB + warpId;   // 全局 warp 序号
    const int numW    = gridDim.x * warpsPB;             // warp 总数

    // 1) running top-32 初始化为 -inf（空集）
    float running = -CUDART_INF_F;

    // 2) warp-stride 扫过输入：每次取连续 32 个候选（lane i 取第 i 个，天然 coalesced）
    for (long base = (long)globalW * WARP; base < n; base += (long)numW * WARP) {
        long idx = base + lane;
        float cand = (idx < n) ? input[idx] : -CUDART_INF_F;  // 越界填 -inf，不影响 top-k
        running = merge_top32(running, cand);                 // §3 的合并（内部先把候选排升序）
    }

    // 3) block 内跨 warp 归约：各 warp 的 top-32 写入 shared
    extern __shared__ float s[];          // warpsPB * 32
    s[warpId * WARP + lane] = running;
    __syncthreads();

    // 由 0 号 warp 顺序把其余 warp 的 top-32 并进来（warpsPB 通常很小，如 8）
    if (warpId == 0) {
        float blk = s[lane];                         // warp 0 自己的
        for (int w = 1; w < warpsPB; ++w) {
            float other = s[w * WARP + lane];        // 第 w 个 warp 的 top-32
            blk = merge_top32(blk, other);           // other 会在 merge 内部被排升序，正确
        }
        // 4) 写出本 block 的 top-32（降序），共 gridDim.x 组
        block_out[blockIdx.x * WARP + lane] = blk;
    }
}
```

提一句第 3 级：那里的 `other` 本来已经是降序的 top-32，传进 `merge_top32` 会被 `warp_bitonic_sort` 再排一次升序——有点冗余但完全正确，省得我为「两个有序段」单独写个 merge 变体。`merge_top32` 因此成了三处通吃的唯一原语，我挺喜欢这点。

**第二级已经在 kernel 里（block 归约）。第三级是跨 block 的最终归并。** 上面每个 block 吐出 32 个值，共 `gridDim.x` 组。再起一个**单 block** kernel，用同样的 `merge_top32` 把这 `gridDim.x` 组 top-32 滚动并归成最终的 top-32：

```cpp
// 单 block 启动：把 block_out 里的 numBlocks 组 top-32 归并成最终 top-32 → out[0..32)。
__global__ void topk32_final(const float* __restrict__ block_out, int numBlocks,
                             float* __restrict__ out) {
    const int lane = threadIdx.x & 31;                 // 仅用一个 warp
    float running = (lane < WARP) ? block_out[lane] : -CUDART_INF_F;
    for (int b = 1; b < numBlocks; ++b)
        running = merge_top32(running, block_out[b * WARP + lane]);
    out[lane] = running;                               // 最终 top-32，降序
}
```

host 侧两步调用即可：

```cpp
int warpsPB = 8, threads = warpsPB * 32;
int blocks  = /* 按 n 与 occupancy 取，如 min(maxBlocks, ceil(n/threads)) */;
size_t shm  = warpsPB * WARP * sizeof(float);
topk32_kernel<<<blocks, threads, shm>>>(d_in, n, d_blockOut);   // 一级+二级
topk32_final<<<1, 32>>>(d_blockOut, blocks, d_out);             // 三级
// d_out[0..32) 即全局 top-32（降序）；要取值就读 lane，要原始下标则在 merge 时一并携带 index。
```

`gridDim.x` 很大时，最后这步也能换成对数轮的并行树形归并（每轮把组数减半、各组并行 `merge_top32`），把 $O(\text{numBlocks})$ 的串行降到 $O(\log \text{numBlocks})$。$k>32$ 时三级结构不变，只是每个 lane 持有 $k/32$ 个值的 register 数组、`merge_top32` 在数组维度展开。

把整条流水的「分块 → 合并」串起来看，就是下面这张三级归约总览（逐级点开）：

```viz
topk-pipeline
```

到这里，整条链路只有「读输入」是访存，其余全在 register/shared 里跑完，没有 atomic、没有数据相关 branch。所以性能完全由「喂数」决定——这正是下一节要发力的地方。

## 5. 工程优化：瓶颈在喂数带宽

bitonic top-k 全程在 register 里、没 atomic 没 divergence，计算本身省得很，**真正的瓶颈是把数据喂进来的访存带宽**。我会盯两点：

**vectorized load。** 让每个 lane 用 `float4` 一次取 16 字节，把 32 个 lane 的一次读拼成 512 字节的连续事务，逼近峰值带宽：

```cpp
const float4* in4 = reinterpret_cast<const float4*>(input + base);
float4 q = in4[lane];                 // 一条指令取 4 个元素
float c0 = q.x, c1 = q.y, c2 = q.z, c3 = q.w;
// 4 个候选可在 register 内先两两取大做一次预筛，再并入 running
```

**double buffering 软件流水。** 维护两组 register，在算第 $N$ 块的 `merge_top32` 时异步预取第 $N{+}1$ 块（`__pipeline_memcpy_async` 或手工预取），把访存延迟藏到比较计算后面。每块计算量固定又无 branch，流水排得非常齐。

**预筛少调几次 merge。** 把几个新候选先在 register 里 `fmax` 折叠，或用一次 `__ballot_sync` 跟 running 当前最小值（lane 31）比一下、把不可能进前 $k$ 的直接滤掉，再调 merge，能明显减少 merge 次数——这是小 $k$ 下最主要的常数优化。

## 6. 适用边界：k 的上限由片上资源决定

bitonic top-k 把容量 $k$ 的有序结构常驻片上，所以 $k$ 的上限根本不是算法问题，而是**寄存器 / 共享内存容量**问题。我拿 A100（GA100，SM80）算一遍。

单 SM 关键参数：寄存器文件 $65536$ 个 32-bit 寄存器；每线程最多 $255$ 个寄存器，超出即溢出到 local memory（实为 DRAM）；最多驻留 $2048$ 线程 $=64$ warp；共享内存可配置至 $164$ KB；寄存器按 warp 分配、粒度 $256$。

**寄存器约束。** register-resident 方案中一个 warp 维护 top-$k$，每 lane 持有 $M=k/32$ 个 `running` 值；合并时还需同时存活一份候选（$M$ 个）与若干临时量（partner、lane、mask、索引等），估每线程寄存器

$$R \approx 2M + 24,\qquad M=\frac{k}{32}.$$

由分配粒度，每 warp 实占寄存器 $R_w=\lceil 32R/256\rceil\times 256$，于是寄存器受限的驻留 warp 数与占用率为

$$W=\min\!\Big(64,\ \big\lfloor 65536/R_w\big\rfloor\Big),\qquad \text{occupancy}=\frac{W}{64}.$$

逐列代入：

| $k$ | $M=k/32$ | $R\approx2M{+}24$ | $R_w=\lceil 32R/256\rceil\times256$ | $W=\lfloor 65536/R_w\rfloor$ | occupancy $=W/64$ |
|---|---|---|---|---|---|
| 32 | 1 | 26 | 1024 | 64 | 100% |
| 64 | 2 | 28 | 1024 | 64 | 100% |
| 128 | 4 | 32 | 1024 | 64 | 100% |
| 256 | 8 | 40 | 1280 | 51 | 80% |
| 512 | 16 | 56 | 1792 | 36 | 56% |
| 1024 | 32 | 88 | 2816 | 23 | 36% |
| 2048 | 64 | 152 | 4864 | 13 | 20% |
| 3680 | 115 | 254 | 8192 | 8 | 12.5% |

硬上限来自 $R\le255$：$2M+24\le255\Rightarrow M\le115\Rightarrow k\lesssim 3680$；再大（$M\ge116,\,R\ge256$）必然溢出到 local memory，破坏「全程片上」的前提、性能急剧下降。$k\le256$ 时占用率在 $80\%$ 以上、比较量 $O(k\log^2 k)$ 仍小；$k$ 由 $512$ 增至 $1024$，占用率从 $56\%$ 降到 $36\%$，对带宽受限的本 kernel 尚可（A100 HBM2e 约 $1.9$ TB/s，二十余 warp 通常足以掩盖访存延迟），但每批候选 $O(k\log^2 k)$ 的排序工作随 $\log^2 k$ 增长，逐步转为计算受限。

**共享内存约束。** block 级归约要把同 block 各 warp 的 top-$k$ 暂存到 shared，每 block 需

$$S_\text{block}=\text{warpsPB}\times k\times 4\ \text{B}.$$

取 $\text{warpsPB}=8$，则 $S_\text{block}=32k$ 字节（$k=256$ 时 $8$ KB，$k=2048$ 时 $64$ KB）；在 $S_\text{SM}=164$ KB 下，驻留 block 数 $\lfloor S_\text{SM}/S_\text{block}\rfloor$ 分别约为 $20$ 与 $2$。若进一步把整个 top-$k$ 放入 shared（block-wide 变体，绕开寄存器上限），单 block 容量约 $S_\text{SM}/4\text{B}\approx 4.2\times10^4$ 个 float，扣除合并所需工作副本后约可容 $k\sim 10^4$——远高于寄存器方案，但此时仅 $1$ block 驻留，且受 bank conflict 与 $O(k\log^2 k)$ 比较量拖累。

**结论。** A100 上 register-resident bitonic top-k 的硬上限大概 $k\lesssim 3680$（溢出前），$k\le256$ 能维持高占用与低比较量；$k$ 超过约 $1024$ 就该转向 [Radix Select](/article/topk-radix-select)（histogram 定位、片上占用与 $k$ 无关）了。这个切换点，就是导言里「自适应选型」那张图的依据。下一篇就讲它。
