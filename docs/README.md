# docs

项目的文档与内容源码，统一放在这里。

| 目录/文件 | 内容 |
|-----------|------|
| `articles/` | 文章的 Markdown 源码，顶部带 YAML frontmatter（`title` / `summary` / `tags`），可从后台「导入 .md」直接建文章、与导出对称。已发布文章的正文也留一份在这里做版本管理。 |
| `viz/` | 可视化组件（`\`\`\`viz <slug>\`\`\`` 围栏内嵌的交互组件）的 Vue SFC 源码。真正的编译产物存在数据库里（后台可视化编辑器就地编译），这里保留可读的源码。 |
| `search.md` | 全站搜索（SQLite FTS5 + simple 分词）的选型与设计文档。 |
| `memory/` | 指向 Claude Code 记忆目录的软链接，**已 gitignore、不入库**（含真实服务器信息）。 |

> 部署相关文档（nginx / HTTPS / systemd / Vaultwarden）与配置放在一起，见 `../deploy/`。

## 约定

- **文章标题走 frontmatter**，正文里**不要**再放同名 `# 一级标题`，否则渲染时标题会重复一次（署名 + 正文各一）。参见 `viz/` 与后台的可视化编辑器。
- 新增/改动 viz 组件后，源码同步更新到 `viz/`，保持这里与数据库里的一致。
