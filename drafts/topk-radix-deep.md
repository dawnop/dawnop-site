# Radix Select：与 k 无关的多趟分桶选择

本文是《TopK 算子优化》流派二的展开。前置结论：大 $k$ 时片上放不下有序候选结构，于是改用 **histogram** 定位第 $k$ 大的元素，片上只需常数大小的计数器，与 $k$ 无关。下面把比特键变换、逐位 select、以及与该算法绑定的几项工程优化（hierarchical atomics、write buffer、task rescheduling、adaptive scaling）讲清楚，并给出核心 kernel。

```viz
topk-radix
```

## 1. 让浮点数可按比特比较

radix select 把数值当无符号整数逐位处理，这要求**比特序与数值序一致**。`int`/`uint` 本就如此（有符号数需翻转符号位）。IEEE-754 `float` 需要一次单调（monotonic）键变换：正数翻转符号位、负数全位取反，使整型比较等价于浮点比较。

```cpp
__device__ __forceinline__ uint32_t f2u_order(float f) {
    uint32_t u = __float_as_uint(f);
    // 正数(符号位0)：置符号位；负数(符号位1)：全取反
    uint32_t mask = (u >> 31) ? 0xffffffffu : 0x80000000u;
    return u ^ mask;
}
__device__ __forceinline__ float u2f_order(uint32_t u) {
    uint32_t mask = (u >> 31) ? 0x80000000u : 0xffffffffu;
    return __uint_as_float(u ^ mask);
}
```

变换后，求"最大的 $k$ 个 float"等价于"最大的 $k$ 个 uint"，可逐位 radix select。取几个值验证：

| 值 | raw `__float_as_uint`（符号位） | ordered key（变换后） |
|---|---|---|
| $-2.0$ | `0xC0000000`（1） | `0x3FFFFFFF` |
| $-0.0$ | `0x80000000`（1） | `0x7FFFFFFF` |
| $+0.0$ | `0x00000000`（0） | `0x80000000` |
| $+1.0$ | `0x3F800000`（0） | `0xBF800000` |
| $+3.0$ | `0x40400000`（0） | `0xC0400000` |

ordered key 按无符号升序排列，恰好还原 $-2<-0<+0<+1<+3$ 的数值序。两条规则的来由：正数（符号位 0）彼此本就按 uint 有序，只需置符号位让它们整体大于负数；负数（符号位 1）**指数/尾数越大、数值反而越小**，序相反，全位取反同时翻转符号位与高低序。有符号 `int` 更简单——只翻符号位（`u ^ 0x80000000`）；`uint` 无需变换。注意必须 **MSB 优先**逐位处理：高位定序、低位细分。

## 2. 逐位 select：完整多趟算法

取 $D=8$（每趟 256 个 bin）。维护三样东西：已确定的高位 `prefix`、还需选出的个数 `kCur`、当前候选缓冲 `cand`（初始为全部 $n$ 个 ordered key）。每一趟（pass）做四步：① 对 `cand` 按当前 8 bit 统计 histogram；② 从最高 bin 向下累加，定位累计首次达到 `kCur` 的 bin（pivot）；③ pivot 之上的 bin 整段进 top-k，更新 `kCur`、把 pivot 拼入 `prefix`；④ 用 filter 把落在 pivot bin 的候选紧凑写入下一趟的 `cand`，候选规模随之 $\times\tfrac{1}{256}$ 缩小。host 侧驱动这条循环：

```cpp
// host 驱动：把 ordered key（已 f2u_order）选出 top-k。device kernel 见下与 §3。
uint32_t prefix = 0;            // 已确定的高位
int kCur = k, n_cur = n;        // 还需个数 / 当前候选数
for (int shift = 32 - D; shift >= 0; shift -= D) {       // 至多 32/D = 4 趟
    cudaMemset(d_hist, 0, 256 * sizeof(int));
    histogram_kernel<D><<<grid, block>>>(d_cand, n_cur, prefix, shift, d_hist);  // §3
    int hist[256];
    cudaMemcpy(hist, d_hist, sizeof(hist), cudaMemcpyDeviceToHost);

    int cum = 0, pivot = 0;                             // top-down 扫描定 pivot
    for (int b = 255; b >= 0; --b) {
        if (cum + hist[b] >= kCur) { pivot = b; break; }
        cum += hist[b];
    }
    kCur   -= cum;                                      // pivot 之上的 bin 已确定进 top-k
    prefix |= (uint32_t)pivot << shift;                 // 锁定本轮 8 bit
    if (kCur == 0 || shift == 0) break;                 // 选满 / 比特用尽

    cudaMemset(d_ncount, 0, sizeof(int));               // filter：缩并出下一趟候选
    filter_kernel<D><<<grid, block>>>(d_cand, n_cur, prefix, shift, d_next, d_ncount);
    cudaMemcpy(&n_cur, d_ncount, sizeof(int), cudaMemcpyDeviceToHost);
    std::swap(d_cand, d_next);
}
// prefix 即第 k 大元素的 ordered-key 阈值；扫原始数组取出所有 >= prefix 的元素
collect_kernel<<<grid, block>>>(d_keys, n, prefix, d_out, d_outcount);
```

filter 把命中当前 prefix 的候选紧凑搬到 `d_next`，用一次 `atomicAdd` 取写出位置（与 §4 的 write buffer 同型，可换 buffer 版减 atomic）：

```cpp
template <int D>
__global__ void filter_kernel(const uint32_t* in, int n, uint32_t prefix, int shift,
                              uint32_t* out, int* out_count) {
    const uint32_t pmask = ~((1u << shift) - 1);        // 高于本轮（含本轮已锁定）的位
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    int stride = gridDim.x * blockDim.x;
    for (; i < n; i += stride) {
        uint32_t key = in[i];
        if ((key & pmask) == prefix)                    // 命中当前 prefix → 留作下一趟候选
            out[atomicAdd(out_count, 1)] = key;
    }
}
```

收尾的 collect 与 filter 同型，只是判据换成「ordered key $\ge$ 阈值」，写出原始下标：

```cpp
__global__ void collect_kernel(const uint32_t* keys, int n, uint32_t thr,
                               int* out_idx, int* out_count) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    int stride = gridDim.x * blockDim.x;
    for (; i < n; i += stride)
        if (keys[i] >= thr) out_idx[atomicAdd(out_count, 1)] = i;   // 收集 top-k 下标
}
```

每趟候选规模缩约 $1/256$，至多 $32/8=4$ 趟即定位精确的第 $k$ 大值。片上只占一个 256 元素的 histogram，**与 $k$ 无关**——这是 radix 能扛 median / 任意分位数的根本。三步里最吃成本的是 histogram 的 atomic 与 collect 的 global 写，下面逐项优化。

## 3. 瓶颈与第一项优化：hierarchical atomics

histogram 的痛点是大量线程往同一组计数器累加，global atomic 争抢严重。解法是把 atomic 留在尽量便宜的层级：

```cpp
// 三级层级：每个 block 在 shared 内聚出局部 histogram，最后一次性合并到 global。
template <int D>
__global__ void histogram_kernel(const uint32_t* keys, int n,
                                  uint32_t prefix, int shift, int* g_hist) {
    constexpr int BINS = 1 << D;
    __shared__ int s_hist[BINS];
    for (int i = threadIdx.x; i < BINS; i += blockDim.x) s_hist[i] = 0;
    __syncthreads();

    const uint32_t pmask = ~((1u << (shift + D)) - 1);   // 高于本轮的位
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    int stride = gridDim.x * blockDim.x;
    for (int i = idx; i < n; i += stride) {
        uint32_t key = keys[i];
        if ((key & pmask) != prefix) continue;           // 不匹配 prefix 的跳过
        int bin = (key >> shift) & (BINS - 1);
        atomicAdd(&s_hist[bin], 1);                       // 便宜的 shared atomic
    }
    __syncthreads();
    // 每个 block 只做 BINS 次 global atomic（而非每元素一次）
    for (int b = threadIdx.x; b < BINS; b += blockDim.x)
        if (s_hist[b]) atomicAdd(&g_hist[b], s_hist[b]); // 常数次 global atomic
}
```

下图直观对比：朴素做法每个线程一次 global atomic；hierarchical 聚合后 global atomic 降到「每 block 几次」。

```viz
topk-atomics
```

warp 内还可再降一层：先用 `__match_any_sync` 把同一 warp 内落入同一 bin 的线程合并计数，再由其中一个 lane 做 shared atomic——把"每元素一次 shared atomic"降到"每 warp 每 bin 一次"：

```cpp
// 替换内层的 atomicAdd(&s_hist[bin], 1)：
unsigned same   = __match_any_sync(0xffffffffu, bin);  // 与本 lane 同 bin 的 lane 掩码
int leader = __ffs(same) - 1;                          // 该组最低 lane 作 leader
int cnt    = __popc(same);                             // 该组人数
if ((threadIdx.x & 31) == leader)
    atomicAdd(&s_hist[bin], cnt);                      // 每 warp 每 bin 仅一次 shared atomic
```

`__match_any_sync` 是 sm_70+ 的指令，按值把 warp 内线程分组，恰好天然契合「按 bin 归并」。

## 4. write buffer：减少最终过滤的 global 写

定位到 pivot 后要扫一遍取出 $\ge$ pivot 的元素。逐个 `atomicAdd` 取输出位置再写 global，atomic 与写带宽都吃紧。改为在 shared 内攒一个 buffer，攒够一批再用一次 `atomicAdd` 申请连续区间、合并写出：

```cpp
// collect 的 write-buffer 版：shared 内攒批，满则一次 atomicAdd 申请连续区间再 coalesced 写出。
template <int BLOCK>
__global__ void collect_buffered(const uint32_t* keys, int n, uint32_t thr,
                                 uint32_t* out, int* g_count) {
    __shared__ uint32_t s_buf[2 * BLOCK];
    __shared__ int s_cnt, s_base;
    if (threadIdx.x == 0) s_cnt = 0;
    __syncthreads();

    int i = blockIdx.x * blockDim.x + threadIdx.x, stride = gridDim.x * blockDim.x;
    for (; i < n; i += stride) {
        if (keys[i] >= thr) s_buf[atomicAdd(&s_cnt, 1)] = keys[i];  // shared 内取槽位（便宜）
        __syncthreads();
        if (s_cnt >= BLOCK) {                          // 攒够一批 → 整批 flush
            if (threadIdx.x == 0) s_base = atomicAdd(g_count, s_cnt);  // 仅一次 global atomic
            __syncthreads();
            for (int t = threadIdx.x; t < s_cnt; t += blockDim.x)
                out[s_base + t] = s_buf[t];            // coalesced 写出
            __syncthreads();
            if (threadIdx.x == 0) s_cnt = 0;
            __syncthreads();
        }
    }
    if (threadIdx.x == 0 && s_cnt) s_base = atomicAdd(g_count, s_cnt);  // 收尾余量
    __syncthreads();
    for (int t = threadIdx.x; t < s_cnt; t += blockDim.x) out[s_base + t] = s_buf[t];
}
```

容量设 `2*BLOCK`、超过 `BLOCK` 才 flush（每次 stride 迭代至多新增 `BLOCK` 个，故不溢出），可把 global atomic / 写的次数降约一个 $2^D$ 因子，而 shared 仅多占一倍。

## 5. task rescheduling：批量场景保 occupancy

批量 TopK（许多独立的小输入各求 top-k）若令每个任务各自迭代到收敛，会出现尾部低 occupancy：少数没收敛的任务占着几个 SM、其余空转。解法是**横向对齐**——所有任务先一起跑第 1 趟 histogram，再一起跑第 2 趟……由一个调度 kernel 统一推进趟次，使每一趟都占满 SM，直到全部收敛。实现上把 per-task 的进度打平成数组，每趟对所有未完成 task 并行处理：

```cpp
struct RadixTask {
    const uint32_t* keys; int n;        // 该任务的输入切片
    uint32_t prefix; int kCur, shift;   // 进度：已锁定高位 / 还需个数 / 当前 bit 偏移
    int done;                           // 是否已收敛
};

// 一趟：每个 block 领一个未完成 task，做「histogram → 定 pivot → 更新进度」，收敛则置 done。
__global__ void schedule_round(RadixTask* tasks, int numTasks) {
    int t = blockIdx.x;
    if (t >= numTasks || tasks[t].done) return;
    RadixTask& tk = tasks[t];
    __shared__ int s_hist[256];
    // 1) 对 tk.keys 中匹配 tk.prefix 的元素，按 tk.shift 处的 8 bit 统计 s_hist
    // 2) 从高 bin 向下累加定 pivot，更新 tk.kCur、tk.prefix |= pivot<<tk.shift、tk.shift -= 8
    // 3) if (tk.kCur == 0 || tk.shift < 0) tk.done = 1;
}
// host：while (未全部 done) schedule_round<<<numTasks, block>>>(tasks, numTasks);
```

每趟所有 block 同时在跑、负载齐头并进，避免了「各自为战」时的尾部空转；趟数被所有 task 中最深的那个界定（仍 $\le 4$）。

## 6. adaptive scaling：对抗坏分布

radix 怕一种输入：所有元素高位 bit 高度雷同（数值挤在一个窄区间），前几轮 histogram 切不动、白跑。对策是随机取一个元素 $a_s$，把所有元素减去它：

```cpp
float as = values[hash(seed) % n];   // 随机基准
// 统计/比较时用 (values[i] - as) 的有序键；偏移不改变相对大小关系，
// 但打散了指数位，使原本高位难区分的元素在前几轮就分开。
```

减去一个基准不改变 top-k 的相对顺序，却让数值的指数位重新散开，前几轮 histogram 就能有效划分。对抗性分布上可有明显加速。

## 7. 算法总览图

把多趟「histogram → 定 pivot → filter 缩并」的循环串起来看，就是下面这张总览（逐趟点开）：

```viz
topk-radix-pipeline
```

## 8. 成本模型与适用边界

**访存形态。** histogram 与 filter / collect 都是顺序扫描，用 `uint4` vectorized load 提升带宽利用；prefix 匹配判断 `(key & pmask) == prefix` 无数据相关 branch（编译为掩码比较），不破坏 warp 一致性。

**趟数与读量。** 32-bit key、$D=8$ 时至多 $\lceil 32/D\rceil=4$ 趟。设每趟候选规模缩约 $1/2^D=1/256$，总读量是几何级数

$$n+\frac{n}{256}+\frac{n}{256^2}+\cdots\approx n\cdot\frac{256}{255}\approx n,$$

即**首趟（读全量 $n$）主导**，后续趟几乎免费。算法因此**带宽受限**：理想耗时 $\approx\dfrac{(n+|\text{top-k}|)\times 4\text{B}}{\text{带宽}}$，A100 HBM2e 约 $1.9$ TB/s。histogram 的 atomic 经 hierarchical + warp 聚合降到「每 block 每 bin 常数次」、collect 的 global 写经 write buffer 合并，两者都不再是瓶颈。

**与 $k$ 无关。** 片上只一个 256-bin histogram（$1$ KB shared），计数器与 $k$ 无关，所以 occupancy 不随 $k$ 退化——这是 radix 对比 bitonic 的根本差异，也使它能直接求 median / 任意分位数（把 `kCur` 设成 $n/2$ 或任意名次即可）。

**交叉点。** [Bitonic Top-K](/article/topk-bitonic-select) 受寄存器限制（A100 上高占用区间 $k\lesssim 256$、硬上限约 $k\le 3680$），其代价随 $k$ 增长；radix select 的成本对 $k$ 近似平坦，故 $k$ 增大到约 $1024$ 以上时 radix 反超，而小 $k$（如 $k\le 256$）下 bitonic 的全寄存器、无 atomic 更快。这一交叉正是主文「自适应选型」按 $k$ 切换算法的依据。
