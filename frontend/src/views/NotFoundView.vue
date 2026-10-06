<template>
  <section class="not-found">
    <span class="code" aria-label="错误 404">404</span>
    <h1>页面不存在</h1>
    <p>地址可能有误，或页面已经移走了。</p>
    <RouterLink to="/">返回首页 <span aria-hidden="true">↗</span></RouterLink>
  </section>
</template>

<style scoped>
.not-found {
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  background: #f7f8f5;
  text-align: center;
}
.code {
  color: #b8c6bc;
  font-size: clamp(80px, 20vw, 140px);
  font-weight: 600;
  line-height: 1;
  letter-spacing: -6px;
}
h1 {
  margin: 24px 0 12px;
  font-size: 26px;
  font-weight: 550;
}
p {
  color: var(--muted);
  font-size: 14px;
}
a {
  display: inline-block;
  margin-top: 24px;
  color: #237b68;
  text-decoration: none;
  font-size: 14px;
}
a span {
  margin-left: 16px;
}
</style>
