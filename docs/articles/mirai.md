---
title: Mirai：把 PyTorch 算子编译成 TensorFlow 自定义算子
summary: 线上还在 TensorFlow，研发全挪到了 PyTorch，中间这道坎怎么迈？mirai 的做法是在 PTX 这一层握手：借 torch.compile 把 PyTorch 函数调优成 Triton kernel，抠出 PTX，再包成一个能直接部署的 TF 自定义算子。这篇拆解从生成到落地的整条流水线，以及每一步背后那些「不这么做就跑不通」的细节。
tags: [AI Infra, 编译器, PyTorch, TensorFlow, CUDA]
---

有那么一段时间，我卡在两套框架中间。

这两年模型越做越大，scale up 成了主旋律。顺着这股风，新的研发差不多全往 PyTorch 上走：生态摆在那儿，社区、工具链、`torch.compile` 这一套，写起来就是顺手。可老业务搬不动，一大批线上服务还稳稳跑在 TensorFlow 上，历史包袱压着，一时半会儿下不来。

坎就卡在这儿。算子在 PyTorch 里研发、调好，最后却要落到还在 TF 上的线上。每出一个，就得有人把它「翻译」成 TF 的 C++ 自定义算子：照着 PyTorch 的实现，手写一遍 CUDA、对一遍 shape、缝一遍反向。又慢又容易出错，写出来的 kernel 性能还常常不如 PyTorch 那边随手 `torch.compile` 出来的。

我越想越觉得，这中间有一层是多余的。两个框架吵得再凶，到了 GPU 面前，说的其实是同一种语言。mirai 就是冲这个来的：把一个 PyTorch 函数，一键编译成能直接部署的 TensorFlow 自定义算子。这套东西现在还跑在某个大厂的线上 base 模型里。

这篇讲它怎么做到。默认你大致知道 `torch.compile`、Triton、CUDA kernel 是什么，但不要求你了解任何一个的内部。

## 那层共同的底：PTX

先说清楚那个「同一种语言」是什么。

PyTorch 和 TensorFlow 各有各的计算图、各有各的运行时，这两套东西几乎不可能直接对话。但它们最终都要把一段 GPU 代码交给同一个 NVIDIA 驱动去跑。这段代码在被驱动接手之前，有一种中间形态叫 **PTX**，是 NVIDIA 的虚拟指令集，介于高层 CUDA 和真正跑在卡上的机器码（SASS）之间。驱动加载 PTX 时，会把它即时编译成当前这张卡的 SASS。

关键就在这：**PTX 不认识 PyTorch，也不认识 TensorFlow。** 它只是一段自包含的 GPU 程序。谁把它喂给驱动都行。所以两个框架真正的交汇点不在图那一层（那一层它们永远谈不拢），而在更低的 PTX 层。mirai 要做的，就是从 PyTorch 侧把编译好的 PTX 抠出来，再在 TF 侧把它重新装回去。

```viz
mirai-ptx-bridge
```

上面这张图点一下就明白：两条框架的路各走各的，直到 PTX 这一层才并到一起。左边 PyTorch 负责「把算子编好、调优好」，右边 TensorFlow 负责「把成品装进线上」，PTX 是它们唯一需要达成一致的东西。想清楚这一点，剩下的都是工程。

## 从一个函数到线上算子

用起来是这样的。你给函数挂一个装饰器，标明它要变成哪个算子：

```python
@mirai.op(name="Pffn")
def pffn(inputs, w_gate, b_gate, w_up, b_up, w_down, b_down):
    inputs_t = inputs.transpose(0, 1)
    gates = torch.bmm(inputs_t, w_gate) + b_gate.unsqueeze(1)
    gates = F.silu(gates)
    vals = torch.bmm(inputs_t, w_up) + b_up.unsqueeze(1)
    outputs = gates * vals
    outputs = torch.bmm(outputs, w_down) + b_down.unsqueeze(1)
    return outputs.transpose(0, 1)

mirai.build(pffn, sample_inputs=[...], ptxas="/usr/local/cuda/bin/ptxas")
```

这个例子是一个 Per-Token 的 SwiGLU（门控前馈），一串批量矩阵乘加一个 SiLU 门。`mirai.build` 跑完，会在 `generated/` 里吐出一整套东西：C++ 的 TF 算子源码、一个编译脚本、一个 Python 包装层、还有按精度归档的 PTX 文件。

把整条路摊开，是分在两台机器上的五个环节。**PyTorch 机器**上生成：编译执行、抠 PTX、生成 TF 侧产物，`build()` 干的就是这前三步。产物拷到 **TF 机器**上落地：编译成 `.so`、加载运行。先整体扫一遍：

```viz
mirai-pipeline
```

点「播放」走一遍，看一个 PyTorch 函数怎么跨过两台机器，变成线上能训能推的 TF 算子。下面挑几个真正踩过坑的环节展开。

## 白嫖 torch.compile 的调优

这一步其实什么 kernel 都没自己写，就一句 `torch.compile`：

```python
COMPILE_OPTIONS = {
    "max_autotune": True,
    "max_autotune_gemm_backends": "TRITON",
    "coordinate_descent_tuning": True,
    "allow_buffer_reuse": False,
    "inplace_buffers": False,
    "autotune_fallback_to_aten": False,
}
compiled = torch.compile(func, dynamic=dynamic, options=COMPILE_OPTIONS)
```

这是整个设计里我最满意的取巧。`torch.compile` 背后的 Inductor 会把你的函数编译成 Triton kernel，而 `max_autotune` 加 `coordinate_descent_tuning` 会让它把各种 kernel 配置（tiling、num_warps 之类）实打实地跑一遍、挑最快的。换句话说，**PyTorch 自己就有一套成熟的自动调优，我为什么要在 TF 侧重写一遍、再手动调一遍？** 直接把它调好的成果搬走就是了。这也正是那个「手写 TF 算子性能还不如 torch.compile」的痛点最好的解法：不跟它比，用它。

往深一层看，这还不只是「省得自己调」，而是 TF1.x 那套根本做不出同样的 kernel。有两道具体的天花板，torch.compile 直接绕了过去。

**一是 GEMM 后面那截 epilogue 的融合。** 拿 PFFN 说，一次 `bmm` 出来，紧跟着要加 bias、过 SiLU、再跟另一路逐元素相乘，这些都是矩阵乘的「尾巴」。TF1.x 的 XLA 融不进去，只能让矩阵乘先把结果写回显存，再起一个个 kernel 去做加法、激活、乘法，中间结果在显存里来回搬。而 Torch2.x 的 Inductor 背后是 Triton、落到 PTX，这种 GEMM + epilogue 的融合它做起来非常自然：矩阵乘的结果还在片上，顺手就把 bias、激活、乘法都算完，再一次性写回。少搬几趟中间结果，带宽就省下来了。融不融合的差别，切一下这张图就看得很直观：

```viz
mirai-fusion
```

**二是 tensorcore 用不用得上。** TF1.x 绑的 CUDA 偏老，不少 shape 没法自动走上 tensorcore，矩阵乘只能退回普通 FP32 单元，白白慢一截。torch.compile 这边可以显式打开 TF32（`torch.backends.cuda.matmul.allow_tf32 = True`），把这些矩阵乘喂进 tensorcore。mirai 生成的产物按 `tf32` 归档，冲的就是这条。

几个选项是有讲究的：

- `max_autotune_gemm_backends="TRITON"` 把矩阵乘也逼到 Triton 上出，而不是回落到 cuBLAS。因为我要抠的是 PTX，得让所有 kernel 都是 Triton 生成的、有 PTX 可抠；回落到 cuBLAS 那种闭源库就断链了。
- `allow_buffer_reuse=False`、`inplace_buffers=False` 关掉了 Inductor 的缓冲区复用和原地写。正常跑当然是复用越多越省显存，但我要的不是省显存，是一份**干净、buffer 不互相别名**的生成代码——这样每个中间张量都清清楚楚，后面才好把它们一一映射到 TF 算子的输入输出上。

然后是一个容易被忽略的细节：**这一步会把前向和反向都跑一遍。**

```python
output = compiled(*sample_inputs)
loss = output.sum()
loss.backward()
```

为什么要多此一举跑个反向？因为 Inductor 是惰性的，你不触发反向，它就不会去编译反向的 kernel。而这套方案生成的算子是要能训练的，不只是推理，所以反向那套 kernel 也得拿到手。这里就故意用 `loss.sum().backward()` 逼 Inductor 把前向、反向两套 kernel 都生成出来，后面各抠一份 PTX。

反过来也成立：如果某个算子只做推理、压根用不上反向，demo 里干脆别写反向的部分就是了。mirai 少生成一个反向算子是小事，更实在的是，前向不必再为了一个不存在的反向去保存中间激活，那笔显存和写回的开销直接省掉。要不要反向，成了一个你按需拨的开关。

## PTX 藏在运行时里

到这儿有个反直觉的坑。Inductor 编译完，会在调试目录里留下一份 `output_code.py`，你可能以为 PTX 就在里面——并不在。那份文件里只有 Python：它描述了「该启动哪个 Triton kernel、传什么参数」，但 Triton kernel 本身是**惰性编译**的，只有到 kernel 第一次真正 launch 的那一刻，Triton 才即时把它编成 PTX。

```viz
mirai-ptx-extract
```

所以静态地读那个文件，永远拿不到 PTX。得让它真的跑一次。mirai 的做法是：先用 AST 把 `output_code.py` **打个补丁**（`hack_triton_file`），在每个 kernel launch 的位置插一段钩子，把 launch 时生成的 PTX 和张量 shape 落盘；然后在一个**独立子进程**里执行这份打了补丁的脚本，让 kernel 真的 launch，钩子就把 PTX 抠出来了。

用子进程而不是在原进程里跑，是为了隔离。原进程为了让 Inductor 吐调试信息，开了一堆 `TORCH_COMPILE_DEBUG` 之类的环境，抠 PTX 这一步用不上，塞进同一个进程只会互相污染，还会在工作目录里拉一堆临时产物。干脆开个干净的子进程，只干「跑一遍、抠 PTX」这一件事。

## 那个最隐蔽的坑：PTX 版本要对得上

抠出 PTX 之后，mirai 会做一件看起来很小、实则救命的事：**校验 PTX 的版本号**。要讲清它为什么救命，得先说清 PTX 的版本到底是怎么回事。

每个 PTX 文件的开头都有一行 `.version 8.2` 这样的指令，声明它遵循哪一版的 PTX ISA。这个版本号跟着 CUDA 走：每次 CUDA 发新版本，就可能引入一版新的 PTX ISA。它俩的对应关系是 NVIDIA 官方文档里的一张**查找表**，不是什么公式，节选几行：

| CUDA | PTX ISA |  | CUDA | PTX ISA |
|---|---|---|---|---|
| 11.7 | 7.7 |  | 12.2 | 8.2 |
| 11.8 | 7.8 |  | 12.4 | 8.4 |
| 12.0 | 8.0 |  | 12.5 | 8.5 |
| 12.1 | 8.1 |  | 12.6 | 8.5 |

眼尖的会发现一个「主版本减 4」的规律：CUDA 11.x 对 PTX 7.x，12.x 对 8.x，次版本对齐。这个口诀在很长一段区间里都成立，但它只是巧合、不是规律——到尾巴上就破了：**CUDA 12.5 和 12.6 都是 PTX ISA 8.5**，12.6 并没有跟着涨成 8.6。所以严格说，只能查表，不能减。mirai 内部图省事用的就是减 4 的写法，覆盖常见版本够用，但这是它埋着的一个小隐患。

真正要命的是**兼容方向**。把 PTX 编成机器码的 `ptxas`，以及运行时加载 PTX 的驱动，各自只认到某一版 PTX ISA。它们的兼容是**单向**的：一个 `ptxas` 能吃下**不高于**自己版本的 PTX，也就是**新 `ptxas` 能编老 PTX，老 `ptxas` 编不动新 PTX**。一旦你把一份 `.version` 偏高的 PTX 递给版本偏低的 `ptxas`，它不会将就，直接撂挑子：

```
ptxas fatal : Unsupported .version 8.4; current version is '8.2'
```

这就把危险的方向点出来了：**在比目标机更新的 CUDA 上生成 PTX**。反过来，只要目标机的 CUDA 不低于生成机，就一定安全。下面这个小工具把这条规则做成了可拨的：左边选生成用的 CUDA、右边选目标机 `ptxas` 的 CUDA，看什么时候兼容、什么时候炸，以及炸出来的正是上面那行 fatal：

```viz
mirai-ptx-version
```

这个坑阴就阴在，两套框架本就装在两台机器上，各自的 CUDA 各自升级，很容易在某次不经意的升级后就错开了。要是没有校验，PTX 版本悄悄超前，问题会一路拖到 TF 机器上跑 `build.sh` 那一刻才炸，离出错的根因已经很远，极难定位。

mirai 的处理比「够用就行」更狠一档：它在生成阶段就把 `TRITON_PTXAS_PATH` 显式钉到**目标机那个 `ptxas`**，让 Triton 直接照着目标的版本吐 PTX，然后当场比对——而且要求**完全相等**，不是「不高于就行」。为什么这么严？因为版本既然是照着目标钉的，就该严丝合缝；一旦不等，八成是那个环境变量没生效（Triton 早被 import、把 `ptxas` 路径缓存住了），这本身就是个该立刻暴露的 bug。于是这道等号，既守住了版本兼容，又顺带盯着「我钉的东西到底有没有生效」。

## 装回 TensorFlow

PTX 到手，剩下的是把它包成一个像模像样的 TF 算子。

mirai 用 Jinja2 模板给每个 kernel 渲染一份**自包含的 C++ TF 自定义算子**：里面有 shape 推断、有把 PTX 载入 GPU 的逻辑、有 CUDA launch 的调用。前向、反向各一份。再渲染一个 `build.sh`，到 TF 机器上一跑就把这些 `.cc` 编成 `.so`。

最后是 Python 包装层。它用 `tf.custom_gradient` 把前向算子和反向算子缝在一起：

```python
@tf.custom_gradient
def pffn(inputs, w_gate, ...):
    outputs = _pffn_fwd(inputs, w_gate, ...)
    def grad(d_outputs):
        return _pffn_bwd(d_outputs, inputs, w_gate, ...)
    return outputs, grad
```

缝完这一下，生成出来的 TF 算子就不只是能前向推理了。它在 TF 的计算图里**可导**，能接着反向传播。一个原本在 PyTorch 里写、`torch.compile` 调优过的算子，就这么作为一等公民塞进了 TF，训练、推理两头都接得上。这正是「历史包袱下不了、研发又都在 PyTorch」这个处境最想要的东西。

`build()` 到这里就收工了，剩下的挪到 TF 机器上。整个流程刻意分成两台机器：一台装 PyTorch 2.x + Triton 负责生成，一台装 TensorFlow 负责落地。因为这两套框架连同各自的 CUDA 依赖，塞进同一个环境往往会打架，分开反而干净。把 `generated/` 拷到 TF 机器，`bash build.sh` 把那些 `.cc` 编成 `.so`；之后在 TF 里 `import` 那个 Python 包装层，这个算子就跟原生 op 没两样，训练、推理都能接。一个 PyTorch 里调好的算子，就这么落到了还在 TF 上的线上。

## 藏在契约里的细节

有几个不在主线上、但少一个就跑不通的地方，值得单独拎出来。

**输入和梯度都强制连续。** 抠出来的 kernel 是按连续内存布局编的，可 PyTorch 的 autograd 回传梯度时，未必给你连续的张量。所以装饰器在前向输入和反向梯度两头都插了一道 `.contiguous()`，靠一个前向是恒等、反向把梯度强制连续的小 autograd 函数（`EnforceContiguousGrad`）实现。这层保证不做，kernel 拿到一个非连续张量，轻则算错、重则越界。

**静态 shape 还是动态 shape，是一道取舍。** 默认走静态：把输入 shape 当成编译期常量烤进 kernel，换来最大程度的优化，代价是 shape 一变就得重新生成。加上 `dynamic=True` 则生成 shape-generic 的算子，一个二进制吃变长的 batch，不用重编，代价是让掉一部分静态优化的空间。

```viz
mirai-shape-mode
```

**每次都清 Inductor 缓存。** Inductor 会缓存编译结果，你第二次跑同一个函数，它可能直接命中缓存、根本不重新吐 `output_code.py`，那 mirai 就抠了个空。所以生成前先把 inductor 缓存清掉，逼它每次都重新生成一份可供抠取的代码。

## 回头看

mirai 说到底不是要取代谁。它不觉得 TF 该被淘汰，也不鼓吹全世界都上 PyTorch。它只认一个现实：在两套栈并存、谁也没法一夜切换的窗口期，总得有一座桥，让 PyTorch 侧调好的算子，能低成本地落到还在 TF 上的线上，训练也好、推理也好，都接得上。

而这座桥能架起来，靠的就是开头那个观察：两个框架在图那一层永远谈不拢，但在 PTX 那一层，它们本就说着同一种语言。找到那个共同的底，剩下的全是把每个阶段的坑一个个填平的功夫。
