# 参与本仓库

这里只放指路，规范本身不在这儿重写一遍（写两处必有一处过期）：

- **本仓的工程约定**（CI 跑什么、lint 基线为何这么松、Python 3.10 基线、依赖钉版本）：
  [CLAUDE.md 第 10 节](./CLAUDE.md#10-工程约定机器强制的部分)。
- **常用命令**（两套后端、前端、质量检查、契约对拍）：[README](./README.md#本地开发)
  与 [CLAUDE.md 第 9 节](./CLAUDE.md#9-常用命令)。
- **特性怎么从想法走到代码**（先写设计草案、实测优先、记录「不做的」）：
  [dawn-lang 的 CONTRIBUTING](https://github.com/dawnop/dawn-lang/blob/main/CONTRIBUTING.md)。
  两仓同一套流程，那边写得完整。

另外两条：

- **提交时绝不加 Claude 署名**（`Co-Authored-By: Claude` / `Claude-Session:` trailer 都不要）。
  已有机器兜底——`commit-msg` hook 提交时拦、CI 的 `secrets` job 再拦一次（见 CLAUDE.md 第 10 节），
  但规矩本身以第 7 节为准。真人协作者的 `Co-Authored-By` 不受影响。
- **后端只迭代 Dawn**：新功能只进 `backend-dawn/`（生产）。`backend/`（FastAPI）已**冻结**为
  回滚目标 + 契约参照，仍在 CI 里跑（确保还能启动），但**不再要求特性同步**——别再往里加新功能。
  改了 Dawn 后端若想验证与冻结基线的差异，跑 `backend-dawn/scripts/contract_*.py` 对拍。
