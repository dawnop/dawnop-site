---
title: 【TopK 优化系列 3】二分阈值
---

> **TopK 优化系列**：[0 总览](/article/topk-optimization) · [1 Bitonic](/article/topk-bitonic-select) · [2 Radix](/article/topk-radix-select) · **3 二分阈值（本篇）**

本文是 TopK 优化系列第 3 篇，讲流派三 **二分阈值 Top-K：count-based、无排序的 warp 选择**。前置场景：海量短向量、每条各自求 top-k（每条长度常小于 1024）。把一条拆给多个 block 不划算，于是**一个 warp 处理一条向量**。求 top-k 不靠定位第 $k$ 大元素，而是**二分一个 threshold** $\tau$，使"不小于 $\tau$ 的元素恰好 $k$ 个"。下面给出完整的 warp 内实现与近似变体。

```viz
topk-threshold
```

## 1. 为什么用 threshold 而不是 sort

一个 warp 32 个 lane 处理长度 $R$ 的一行：每个 lane 负责 $R/32$ 个元素（lane $i$ 取下标 $i, i+32, i+64,\dots$，天然 coalesced）。在这种"一条向量铺在一个 warp 上"的布局里，sort 需要 lane 间反复交换，而**统计有多少元素过某个 threshold**只需各 lane 本地比较、再做一次 warp reduction——后者廉价得多。于是把"求第 $k$ 大"转化为"二分 threshold 使过线者恰好 $k$ 个"。

## 2. warp 内求上下界与计数

二分前先求该行的 min/max 作为初始区间，用 `__shfl_xor_sync` 树形 reduction：

```cpp
__device__ __forceinline__ void warp_minmax(float v, float& mn, float& mx) {
    const unsigned FULL = 0xffffffffu;
    mn = mx = v;
    #pragma unroll
    for (int j = 16; j > 0; j >>= 1) {
        mn = fminf(mn, __shfl_xor_sync(FULL, mn, j));
        mx = fmaxf(mx, __shfl_xor_sync(FULL, mx, j));
    }
    // reduction 后所有 lane 都持有全行的 mn/mx
}
```

$R>32$ 时每 lane 持 `vals[L]`，先在 lane 内折叠、再 warp reduction（`find_threshold` 用的就是它）：

```cpp
__device__ __forceinline__ void warp_minmax_arr(const float* vals, int L,
                                                float& mn, float& mx) {
    mn = mx = vals[0];
    #pragma unroll
    for (int t = 1; t < L; ++t) { mn = fminf(mn, vals[t]); mx = fmaxf(mx, vals[t]); }
    #pragma unroll
    for (int j = 16; j > 0; j >>= 1) {                 // 跨 lane 归约
        mn = fminf(mn, __shfl_xor_sync(0xffffffffu, mn, j));
        mx = fmaxf(mx, __shfl_xor_sync(0xffffffffu, mx, j));
    }
}
```

计数是核心。每个 lane 把自己负责的元素与 $\tau$ 比较，本地累加，再 warp 求和。当一行恰好 32 个元素（每 lane 一个）时，可用最优雅的 `ballot + popcount`：

```cpp
// 一行 32 元素：每 lane 一个值 v。数出 >= tau 的个数。
__device__ __forceinline__ int count_ge_32(float v, float tau) {
    unsigned mask = __ballot_sync(0xffffffffu, v >= tau);  // 每 lane 投一票
    return __popc(mask);                                    // 一条指令数 1
}
```

一行更长（$R>32$，每 lane 持有数组 `vals[L]`，$L=R/32$）时，本地计数 + warp 求和：

```cpp
__device__ __forceinline__ int count_ge(const float* vals, int L, float tau) {
    int local = 0;
    #pragma unroll
    for (int t = 0; t < L; ++t) local += (vals[t] >= tau);
    // warp reduction 求和
    #pragma unroll
    for (int j = 16; j > 0; j >>= 1)
        local += __shfl_xor_sync(0xffffffffu, local, j);
    return local;   // 所有 lane 得到全行计数
}
```

两种写法都**无数据相关 branch**：比较结果是 0/1，`ballot`/加法把它无 branch 地汇总。

## 3. 二分主循环

```cpp
// 一个 warp 处理一行 row[0..R)，求 top-k 的 threshold tau。
__device__ float find_threshold(const float* row, int R, int k,
                                float* vals, int L) {
    // 1) 各 lane 把自己负责的元素载入 register 数组 vals[L]（coalesced 访存）
    int lane = threadIdx.x & 31;
    #pragma unroll
    for (int t = 0; t < L; ++t) {
        int idx = lane + t * 32;
        vals[t] = (idx < R) ? row[idx] : -CUDART_INF_F;   // 越界填 -inf
    }
    // 2) 初始区间
    float lo, hi; { float mn, mx; warp_minmax_arr(vals, L, mn, mx); lo = mn; hi = mx; }
    // 3) 二分：使 count(>= tau) 收敛到 k
    float tau = hi;
    for (int it = 0; it < 30; ++it) {
        tau = 0.5f * (lo + hi);
        int c = count_ge(vals, L, tau);
        if (c == k) break;
        if (c > k) lo = tau;   // 选中过多 -> 抬高 threshold
        else       hi = tau;   // 选中过少 -> 降低 threshold
    }
    return tau;
}
```

二分成立的前提是单调性：`count_ge(τ)` 对 $\tau$ 单调不增（$\tau$ 越高、过线者越少），故存在使计数落到 $k$ 的区间，对半收缩必然逼近它。收敛后，再扫一遍把 $\ge\tau$ 的元素写出即得 top-k。整个过程不使用 shared memory、不写中间结果，register 占用低，一个 SM 上可同时跑很多 warp（很多行），吞吐极高。

## 4. early-stop：用精度换速度

上面的循环要二分到 `c == k` 才停，最坏需 ~30 轮（浮点精度上限）。但在稀疏化训练等场景，结果多一个少一个元素无关紧要。于是**固定轮数**提前停手，接受一个近似 threshold：

```cpp
const int ITERS = 8;                       // 固定 8 轮，不再判 c==k
for (int it = 0; it < ITERS; ++it) {
    float tau = 0.5f * (lo + hi);
    int c = count_ge(vals, L, tau);
    if (c > k) lo = tau; else hi = tau;
}
float tau = lo;                            // 取下界，保证至少 k 个（偏多一点）
```

固定 8 轮把 threshold 定位到 $1/2^8$ 的相对精度，实际选中个数与 $k$ 的偏差通常在个位百分比。轮数是一个直接的精度/速度旋钮：轮数越少越快、误差越大。因为去掉了 `c==k` 的提前 break，每个 warp 的轮数完全一致，**消除了 warp 间的尾部不齐**，对批量吞吐很友好。

## 5. 收集结果

threshold 定好后收集。若只需"哪些位置入选"，再扫一遍写掩码即可；若要紧凑输出，用 warp 内 prefix sum（`__ballot_sync` + `__popc` 算 lane 内偏移）定位每个入选元素的写出位置，避免逐个 atomic：

```cpp
// 收集一行：把 >= tau 的元素紧凑写到 out。
for (int t = 0; t < L; ++t) {
    bool take = vals[t] >= tau;
    unsigned m = __ballot_sync(0xffffffffu, take);
    int slot = base + __popc(m & ((1u << lane) - 1));   // 本 lane 之前有几个
    if (take) out[slot] = vals[t];
    base += __popc(m);                                   // 整批推进写指针
}
```

## 6. 完整 kernel：一个 warp 一行、批量并行

把前面的零件拼成批量 kernel：$B$ 行、每行 $R$ 个，一个 warp 处理一行、行号 = 全局 warp 号。每个 warp 把自己这行载入 register 数组 `vals[L]`，二分出阈值，再 collect 写出。

```cpp
#include <math_constants.h>   // CUDART_INF_F
// B 行 × R；一个 warp 一行。out_idx[row*k ..] 写该行 top-k 的下标。
template <int L>              // L = R/32，编译期常量（决定 register 数组长度）
__global__ void rowwise_topk(const float* __restrict__ rows, int R, int k, int B,
                             int* __restrict__ out_idx) {
    const int gwarp = (blockIdx.x * blockDim.x + threadIdx.x) >> 5;  // 全局 warp 号 = 行号
    const int lane  = threadIdx.x & 31;
    if (gwarp >= B) return;
    const float* row = rows + (size_t)gwarp * R;

    float vals[L];
    float tau = find_threshold(row, R, k, vals, L);   // §3：载入 + 二分

    // collect：ballot 前缀和定位写出槽位（§5），紧凑写该行 top-k 的原始下标
    int base = 0;
    int* out = out_idx + (size_t)gwarp * k;
    #pragma unroll
    for (int t = 0; t < L; ++t) {
        bool take = vals[t] >= tau;
        unsigned m = __ballot_sync(0xffffffffu, take);
        int slot = base + __popc(m & ((1u << lane) - 1));
        if (take && slot < k) out[slot] = lane + t * 32;
        base += __popc(m);
    }
}
```

host 侧按「行 → warp」铺满：

```cpp
int threads = 256;                                  // 8 warp/block
int blocks  = (B * 32 + threads - 1) / threads;
rowwise_topk<L><<<blocks, threads>>>(d_rows, R, k, B, d_out);   // L = R/32 编译期定
```

没有 shared memory、没有 atomic、没有 block 间同步：每个 warp 独立吃一行、register 内二分、ballot 写出。$B$ 越大可调度的 warp 越多、越能填满 SM——这正是 row-wise 海量小输入下它吞吐极高的根本。

## 7. 算法总览图

把「一 warp 一行、register 内二分、ballot 收集」的批量结构串起来看：

```viz
topk-threshold-pipeline
```

## 8. 成本模型与适用边界

**访存与计算形态。** 布局保证 coalesced 访存：相邻 lane 读相邻地址。整行一次性载入 register（`vals[L]`），二分每一轮都在 register 内比较、**不再触碰显存**——每行的显存流量仅 $R$ 次读 + $k$ 次写。计算上每行 $\text{ITERS}$ 轮、每轮一次 `count_ge`（$L$ 次本地比较 + 5 步 warp reduction），**与 $k$ 无关**。

**寄存器约束（A100）。** 每 lane 持 `vals[L]`、$L=R/32$，加临时量估每线程 $R_\text{thread}\approx L+20$；occupancy 公式同 [Bitonic 篇](/article/topk-bitonic-select) §6：$R_w=\lceil 32R_\text{thread}/256\rceil\times256$，$W=\min(64,\lfloor 65536/R_w\rfloor)$。代入：

| 行长 $R$ | $L=R/32$ | $R_\text{thread}\approx L{+}20$ | $W$ | occupancy |
|---|---|---|---|---|
| 128 | 4 | 24 | 64 | 100% |
| 256 | 8 | 28 | 64 | 100% |
| 512 | 16 | 36 | 51 | 80% |
| 1024 | 32 | 52 | 36 | 56% |
| 2048 | 64 | 84 | 23 | 36% |
| 4096 | 128 | 148 | 13 | 20% |
| 7168 | 224 | 244 | 8 | 12.5% |

瓶颈是**行长 $R$**（不是 $k$）：$R$ 越大 `vals[L]` 越占寄存器、occupancy 越低；$R\gtrsim 7000$（$L\gtrsim 224$）逼近 255 寄存器硬墙，溢出后失效。

**迭代轮数。** 精确收敛需约 $\lceil\log_2\frac{hi-lo}{\epsilon}\rceil$（float 上至多约 $30$）轮；early-stop 固定 $\text{ITERS}$（如 $8$）把阈值定位到 $1/2^8$ 相对精度，且所有 warp 轮数一致、无尾部发散，对批量吞吐最友好。

**适用边界。** 最佳区间是「$R$ 不大（$\lesssim 1024$）、batch $B$ 极多」的 row-wise 场景（逐行 softmax 后取 top-k、MoE 路由、稀疏 attention）。$R$ 过大应回到单行用多 block 的 [Radix Select](/article/topk-radix-select)；$k$ 接近 $R$ 时二分仍有效但优势减弱。与 radix 的分工：radix 适合「单个大数组、大 $k$」，本法适合「海量小行、各自小 $k$」。
