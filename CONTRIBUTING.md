# 参与本仓库

这里只放指路，规范本身不在这儿重写一遍（写两处必有一处过期）：

- **本仓的工程约定**（CI 跑什么、lint 基线为何这么松、Python 3.10 基线、依赖钉版本）：
  [CLAUDE.md 第 10 节](./CLAUDE.md#10-工程约定机器强制的部分)。
- **常用命令**（两套后端、前端、质量检查、契约对拍）：[README](./README.md#本地开发)
  与 [CLAUDE.md 第 9 节](./CLAUDE.md#9-常用命令)。
- **特性怎么从想法走到代码**（先写设计草案、实测优先、记录「不做的」）：
  [dawn-lang 的 CONTRIBUTING](https://github.com/dawnop/dawn-lang/blob/main/CONTRIBUTING.md)。
  两仓同一套流程，那边写得完整。

两条不在别处、也不会被 CI 拦住的：

- **提交时绝不加 Claude 署名**（`Co-Authored-By` / `Claude-Session` trailer 都不要）。
- **改后端要改两套**：`backend-dawn/`（Dawn，生产）与 `backend/`（FastAPI，回滚目标 +
  契约参照）。只改一边，回滚那天才会发现——那时候你正忙着别的。
  改完跑 `backend-dawn/scripts/contract_*.py` 对拍。
