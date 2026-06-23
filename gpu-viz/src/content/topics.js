// 主题数据：每个主题 = 讲解文本（+可选代码/模式）。新增主题在此追加即可。
// hasScene 标记是否已有对应 3D 场景；暂未实现的会在视口显示「开发中」占位。
export const topics = [
  {
    id: 'thread-model',
    title: '线程模型',
    icon: '▦',
    hasScene: true,
    modes: [
      { id: 'blocks', label: 'Block 划分' },
      { id: 'warps', label: 'Warp 调度' },
      { id: 'thread', label: '单线程' },
    ],
    desc: `CUDA 把并行工作组织成三级层次：<b>Grid → Block → Thread</b>。
一次 kernel 启动是一个 Grid，Grid 由若干 <b>Block</b> 组成，每个 Block 内有成百上千个 <b>Thread</b>。
同一 Block 内的线程共享 <code>shared memory</code> 且可 <code>__syncthreads()</code> 同步；不同 Block 之间相互独立、不保证执行顺序。
硬件实际以 <b>warp</b>（32 个线程）为单位调度：一个 warp 内的线程同步执行同一条指令（SIMT），分支不一致会导致 warp divergence。
切换右侧模式，分别观察「网格如何切成 Block」「warp 如何逐组被调度」「单个线程的位置」。`,
    legend: '提示：拖拽旋转，滚轮缩放。绿色 = 当前被调度的 warp。',
  },
  {
    id: 'tiling',
    title: '分块策略',
    icon: '▤',
    hasScene: false,
    desc: `<b>Tiling（分块）</b>把大矩阵切成能放进 <code>shared memory</code> 的小块，
让一个 Block 协作把 tile 从全局显存搬到片上、复用多次，从而把算法从「访存受限」推向「计算受限」。
经典例子是分块矩阵乘：每个 Block 负责输出的一个 tile，循环加载 A、B 的 tile 到 shared memory，
块内线程各算一个/几个输出元素。关键权衡：tile 越大复用越高，但受 shared memory 与寄存器容量限制。
<i>（该主题 3D 场景开发中）</i>`,
    legend: '将演示：global → shared 的 tile 搬运与复用。',
  },
  {
    id: 'pipeline',
    title: '流水线',
    icon: '⇥',
    hasScene: false,
    desc: `GPU 用 <b>延迟隐藏</b>而非乱序执行来填满流水线：当一个 warp 因访存停顿时，
调度器立刻切到另一个就绪的 warp，靠海量 warp 的「超额订阅」把访存/计算延迟藏起来。
更进一步是 <b>软件流水线</b>（如 <code>cp.async</code> + double buffering）：在计算当前 tile 的同时，
异步预取下一块数据，让搬运与计算重叠。<i>（该主题 3D 场景开发中）</i>`,
    legend: '将演示：warp 停顿切换 + 计算/搬运重叠。',
  },
  {
    id: 'cache',
    title: '缓存层级',
    icon: '▥',
    hasScene: false,
    desc: `GPU 的存储层级（由近到远）：<b>寄存器 → Shared/L1 → L2 → 全局显存(HBM)</b>，
容量递增、带宽与延迟递减。Shared memory 与 L1 共用片上 SRAM，可按需配比。
合并访问（coalescing）：一个 warp 的 32 个线程访问连续地址时合并成少量事务，是带宽利用的关键。
<i>（该主题 3D 场景开发中）</i>`,
    legend: '将演示：各级容量/延迟对比与合并访问。',
  },
  {
    id: 'ptx',
    title: 'PTX 指令',
    icon: '⌗',
    hasScene: false,
    desc: `<b>PTX</b> 是 NVIDIA 的虚拟 ISA（介于 CUDA C++ 与真实机器码 SASS 之间），
由 <code>ptxas</code> 编译到具体架构的 SASS。读 PTX 能看清线程如何取自己的 <code>tid</code>、
做地址计算与访存。下面是向量加法 kernel 的 PTX 片段：<i>（该主题 3D 场景开发中）</i>`,
    code: `.visible .entry vecAdd(
    .param .u64 A, .param .u64 B, .param .u64 C, .param .u32 N)
{
    .reg .pred  %p1;
    .reg .f32   %f<4>;
    .reg .b32   %r<6>;
    .reg .b64   %rd<11>;

    mov.u32     %r1, %ctaid.x;      // blockIdx.x
    mov.u32     %r2, %ntid.x;       // blockDim.x
    mov.u32     %r3, %tid.x;        // threadIdx.x
    mad.lo.s32  %r4, %r1, %r2, %r3; // i = blockIdx.x*blockDim.x + threadIdx.x
    setp.ge.s32 %p1, %r4, %r5;      // if (i >= N)
    @%p1 bra    DONE;               //   return;

    // 地址 = base + i*4，加载、相加、存回
    cvta.to.global.u64 %rd4, %rd1;
    ld.global.f32 %f1, [%rd4];
    ld.global.f32 %f2, [%rd7];
    add.f32     %f3, %f1, %f2;
    st.global.f32 [%rd10], %f3;
DONE:
    ret;
}`,
    legend: '将联动 3D 高亮：每条指令对应线程的取数/算/存。',
  },
]
