# dawnop-site

简洁、干净的个人博客网站，内置需登录的后台文件管理（网盘）模块。线上：[dawnop.com](https://dawnop.com)。

- **博客前台**：首页、文章页（Markdown + 内嵌 LaTeX + 可交互可视化）、内容页/分类页、标签聚合，以及 ⌘K 全站搜索（支持中文、拼音）。
- **后台**（需登录）：文章/页面管理（含 Markdown 上传/下载）、自建文件管理器（图片/文本上传下载与在线预览，文件存于七牛云 Kodo）、站点监控。
- **WebDAV**：`dav.dawnop.com` 可挂载读写，供 Finder / rclone / RaiDrive 等直接访问网盘。
- **前后端分离**：后端为纯 JSON API，便于后续接入其他客户端。

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | **[Dawn](https://github.com/dawnop/dawn-lang)**（自制语言，编译到 JVM 字节码） |
| DB | SQLite |
| 鉴权 | OAuth2 Password + JWT |
| 对象存储 | 七牛云 Kodo |
| 前端 | Vue 3 + Vite + Element Plus |
| Markdown / LaTeX | markdown-it + KaTeX + highlight.js |
| 搜索 | SQLite FTS5 + wangfenjin/simple 扩展（中文字级 + 拼音） |
| 部署 | Nginx + systemd |

> **后端跑在 Dawn 上。** 站点最初是 FastAPI + SQLAlchemy 写的；M6 用 strangler-fig 的方式
> 逐个路由迁到 [dawn-lang](https://github.com/dawnop/dawn-lang)（同作者的自制语言），
> 2026-07 全量切流，uvicorn 已退役。`backend/` 里的 FastAPI 实现**仍在维护**：它是紧急
> 回滚目标（`backend-dawn/deploy/rollback-to-fastapi.sh`），也是 `backend-dawn/scripts/contract_*.py`
> 对拍 Dawn 实现时的参照。两套的测试都在 CI 里跑。

## 目录结构

```
backend-dawn/   生产后端（Dawn）。src/ 内联 test 块，dawn.toml 声明 java-deps
backend/        FastAPI 实现：回滚目标 + 契约参照
frontend/       Vue 3 + Vite
deploy/         nginx / systemd / 证书 / vaultwarden
docs/           设计文档、文章、viz 组件
scripts/        fetch-dawn.sh（按 .dawn-version 解析工具链）
.dawn-version   钉住的 Dawn 编译器版本（单一事实源，CI 与本地共用）
```

## 本地开发

### 后端（Dawn，生产用的这套）

```bash
./backend-dawn/build.sh          # 按 .dawn-version 取编译器 → 跑 60 个测试 → 打 jar
java -jar backend-dawn/backend-dawn.jar
```

编译器由 `scripts/fetch-dawn.sh` 按根目录 `.dawn-version` 自动下载并缓存到 `.dawn/`，
无需本地装 dawn-lang。同时改语言和后端时，把 `.dawn-version` 写成 `main`（克隆现编），
或 `DAWN_BIN=~/workspace/dawn-lang/bin/dawn ./backend-dawn/build.sh`。

### 后端（FastAPI，回滚目标）

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # 生产是 Python 3.10，代码须保持 3.10 兼容
pip install -r requirements-dev.txt                 # 生产只装 requirements.txt
cp .env.example .env                                # 填入七牛密钥、JWT secret 等
python scripts/seed_admin.py                        # 初始化管理员账号
python scripts/fetch_simple_ext.py                  # 可选：中文分词扩展（缺失则搜索降级为 trigram/LIKE）
uvicorn app.main:app --reload                       # http://127.0.0.1:8000  文档 /docs
pytest                                              # 120 个测试
```

### 前端

```bash
cd frontend
npm install
npm run dev           # Vite 开发服务器
npm run build         # 产出 dist/
npm run lint          # eslint
npm run format        # prettier
```

### 质量检查（CI 跑的就是这些）

```bash
ruff check . && ruff format --check .   # Python，配置见 pyproject.toml，target 钉 3.10
cd frontend && npm run lint && npm run format:check
```

## 配置与安全

- 敏感信息（七牛 AccessKey/SecretKey、Bucket、JWT secret、管理员密码等）放在 `backend/.env`，**不提交**。
- 仓库仅提交 `.env.example` 模板。

## 部署

CI 在每次 push 到 main 时构建并测试后端，产出 `backend-dawn.jar` + `lib/` 作为 artifact；
`backend-dawn/deploy/deploy.sh` 拉取该 artifact 部署（自带健康检查与失败自动回滚）。
前端 `npm run build` 后把 `dist/` 传到服务器。细节见 [`deploy/README.md`](./deploy/README.md)。

## 更多

- 开发约定与架构细节：[CLAUDE.md](./CLAUDE.md)
- 全站搜索的选型与设计：[docs/search.md](./docs/search.md)

## 许可证

[MIT](./LICENSE)。
