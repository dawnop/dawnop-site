<!-- viz-name: 关于页时间线 -->
<script setup>
// 关于页的经历时间线（扁平：灰线 + 圆点，仅「现在」用一点蓝）。
// 纯静态展示，不引入 vue 之外的任何依赖。改文案后需重新编译（见 docs/viz 说明）。
// 每个节点的正文是段落数组，段落可带一个行内链接。
const nodes = [
  {
    phase: '本科',
    title: '一切从一个在线编辑器开始',
    paras: [
      {
        text: '赶上互联网还热的时候，做前后端。大创项目做了个 p5home：一个能在浏览器里直接写、编译、跑 p5.js 的在线编辑器，前端 Vue、后端 Spring Boot。站早就下线了，但它是后面这一串故事的起点。',
        link: { text: 'p5home', href: 'https://github.com/dawnop/p5home-frontend' },
      },
      {
        text: '当时特别想给它加代码补全，觉得这东西太炫了。一查才知道，补全背后牵着 LSP、牵着编译那一整套。人就这么被勾进了编译器的世界。',
      },
      {
        text: '之后纯凭兴趣做编译器相关的东西，写着写着也有了语言偏好。最喜欢 Kotlin：简洁、实用，是那种「更好的 Java」；相比之下 Scala 太学院派，符号和抽象都重，写起来累。Kotlin 主战场在 JVM，于是又把 JVM 摸了个遍：字节码、类加载这些大厂面试常问的基本都门儿清，还试着在 JVM 上自制了一门小语言。',
      },
    ],
  },
  {
    phase: '研究生',
    title: '篡改检测，顺便当了回运维',
    paras: [
      {
        text: '课题是图像篡改检测。模型里有几步没有现成的高效算子，我就自己写了两个：一个高效的像素聚类算子，一个邻接性判断算子。这些工作后来进了一篇开源论文。',
        link: { text: 'HRGR-IMD', href: 'https://github.com/OUC-VAS/HRGR-IMD' },
      },
      {
        text: '那会儿还兼职给实验室看机器。说是 GPU 集群，其实就三台 3090，存算分离：一台既当 master 又当 worker，另外两台纯 worker，数据全放 NAS，要用就走网络拉。NAS 的 SSD 扛不住，我就在 worker 上加本机缓存、在 NAS 上加内存缓存，把性能问题摁下去。第一次认真跟 GPU 和系统性能打交道，就是从这儿开始的。',
      },
    ],
  },
  {
    phase: '毕业后',
    title: '一头栽进 AI Infra',
    paras: [
      {
        text: '进了大厂，因为兴趣做起 AI Infra，这方向现在需求大得很，也算侥幸吃到了时代的红利。',
      },
      {
        text: '比较得意的是 mirai：把 PyTorch 函数编译成能直接部署的 TensorFlow 自定义算子，底层跑的是 Triton PTX kernel。加个 @mirai.op，让 torch.compile 把 kernel 生成好，再把 PTX 抠出来、包成 C++ 的 TF op。到现在它还跑在几个业务的线上 base 模型里。',
        link: { text: 'mirai', href: 'https://github.com/dawnop/mirai' },
      },
    ],
  },
  {
    phase: '现在',
    title: '抠训练的性能',
    current: true,
    paras: [
      {
        text: '主要做训练的性能优化。nsys 看端到端、ncu 定位算子瓶颈；要快就用 Triton 写算子，要极致就上 CUDA、CUTLASS、CuTe 一点点抠。能复用 torch 生态就复用，把算子嵌进 torch.compile，让它顺着现成的图优化一起跑。',
      },
    ],
  },
]

const closing =
  'AI 时代来了。对我这种文字功底一般的人反倒是好事，总算能把这些年攒下的经验讲得生动点。这个博客就是干这个的。'
</script>

<template>
  <div class="tl">
    <ol class="tl-list">
      <li
        v-for="(n, i) in nodes"
        :key="i"
        class="tl-item"
        :class="{ current: n.current }"
      >
        <div class="tl-phase">{{ n.phase }}</div>
        <div class="tl-title">{{ n.title }}</div>
        <p v-for="(pa, j) in n.paras" :key="j" class="tl-body">
          {{ pa.text
          }}<a
            v-if="pa.link"
            class="tl-link"
            :href="pa.link.href"
            target="_blank"
            rel="noopener"
            >{{ pa.link.text }} ↗</a
          >
        </p>
      </li>
    </ol>
    <p class="tl-closing">{{ closing }}</p>
  </div>
</template>

<style scoped>
.tl {
  margin: 4px 0;
}
.tl-list {
  list-style: none;
  margin: 0;
  padding: 0;
  position: relative;
}
/* 贯穿的竖线 */
.tl-list::before {
  content: '';
  position: absolute;
  left: 5px;
  top: 8px;
  bottom: 8px;
  width: 2px;
  background: var(--border, #ebedf0);
}
.tl-item {
  position: relative;
  padding: 0 0 30px 30px;
}
.tl-item:last-child {
  padding-bottom: 0;
}
/* 节点圆点 */
.tl-item::before {
  content: '';
  position: absolute;
  left: 0;
  top: 5px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #fff;
  border: 2px solid var(--border, #d0d7de);
  box-sizing: border-box;
}
.tl-item.current::before {
  border-color: var(--accent, #1677ff);
  background: var(--accent, #1677ff);
}
.tl-phase {
  font-size: 0.8rem;
  letter-spacing: 0.04em;
  color: #8b949e;
}
.tl-title {
  margin: 3px 0 0;
  font-size: 1.08rem;
  font-weight: 650;
  line-height: 1.45;
  color: var(--fg, #1a1a1a);
}
.tl-body {
  margin: 9px 0 0;
  font-size: 0.98rem;
  line-height: 1.8;
  color: var(--muted, #57606a);
}
.tl-link {
  margin-left: 6px;
  white-space: nowrap;
  color: var(--accent, #1677ff);
  text-decoration: none;
  border-bottom: 1px solid color-mix(in srgb, var(--accent, #1677ff) 35%, transparent);
}
.tl-link:hover {
  border-bottom-color: var(--accent, #1677ff);
}
.tl-closing {
  margin: 28px 0 0;
  padding-top: 22px;
  border-top: 1px solid var(--border, #ebedf0);
  font-size: 0.98rem;
  line-height: 1.8;
  color: var(--muted, #57606a);
}
</style>
