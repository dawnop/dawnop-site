# backend-dawn

dawnop.com 博客后端的 **Dawn 重写**（dawn-lang M6，计划见 dawn-lang 仓库 `docs/m6.md`）。
与 `backend/`（FastAPI，迁移期参照实现）共用同一 SQLite 库与七牛空间，nginx 按路由灰度切流。

- 依赖：`lib/` 下 vendored 单 jar（sqlite-jdbc、jBCrypt——零传递依赖白名单，见 m6.md §2 G1），
  通过 `dawn run/test/build --cp` 挂载。
- 测试：`dawn test --cp "lib/sqlite-jdbc-3.36.0.3.jar:lib/jbcrypt-0.4.jar" backend-dawn`
- 模块：`src/sql.dawn`（JDBC 薄包装）……（随刀补全）
