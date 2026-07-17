# 全站搜索 · 技术选型与设计

> 面向博客文章的站内全文检索。2026-07 上线，生产已验证。
> 本文记录**为什么这么选**与**详细设计**；CLAUDE.md 只保留一句指针。

## 1. 需求与约束

- 搜索**已发布**文章的标题 / 摘要 / 正文，按相关度排序，返回高亮片段。
- 中文为主，兼顾英文、代码符号（如 `CUDA`、`bitonic`）。
- 部署环境：**单台 4 核 4G**，同机还跑着 FastAPI + Nginx + 前端静态，数据库是 **SQLite（无 Alembic）**。
  → 内存预算紧张，**不愿为搜索单独常驻一个重进程**。
- 文章数量级：几十~几百篇（个人博客），不是大规模语料。

## 2. 方案选型

### 2.1 为什么不是独立搜索引擎（Meilisearch / Elasticsearch / 外部服务）

| 维度 | 独立引擎（以 Meilisearch 为例） | SQLite FTS5（选中） |
|------|-------------------------------|--------------------|
| 部署形态 | 独立进程 / 服务，要额外守护、开端口、做数据同步 | **进程内**，随后端起，无额外服务 |
| 常驻内存 | 基线约 100MB+，随索引增长 | **近零**（就是 SQLite 自己） |
| 数据一致性 | 要写「双写 / 同步管道」把文章推进引擎 | **触发器自动同步**，天然一致 |
| 运维复杂度 | 版本、备份、故障恢复都多一套 | 跟着 `dawnop.db` 走 |
| 检索质量 | 更强（typo 容错、facet、同义词…） | 够用（bm25 + 前缀 + 拼音） |

结论：语料小、机器小、要简单 → **进程内的 FTS5 完胜**。独立引擎的强项（海量数据、复杂检索）本项目用不上，却要付出常驻内存和运维成本。

### 2.2 中文分词：trigram vs jieba vs simple

FTS5 自带 `trigram`，但对中文有硬伤；社区常用 wangfenjin/simple 扩展补齐。三者实测对比：

| 方案 | 原理 | 2 字词（`内存`） | 拼音（`neicun`/`nc`） | 内存开销 | 说明 |
|------|------|:---:|:---:|:---:|------|
| `trigram`（内置） | 连续 3 字符子串 | ❌ 需≥3字 | ❌ | ~0 | `内存` 才 2 字，命不中；纯 LIKE 兜底 |
| simple 的 `jieba_query()` | 词级分词 | ✔ | ✔ | **~200MB 常驻** + ~13MB 词典 | 词典重，且**并不能消除跨词假阳性**（见下） |
| simple 的 `simple_query()`（**选中**） | 字级 + 拼音 | ✔ | ✔ | **~0**（~900KB 的 .so） | 召回导向，字级 AND |

**两处曾经的误判，已纠正（记录以免重犯）：**
- ❌ 以为 jieba「只几十 MB」→ ✅ 实测**常驻约 200MB**，对 4G 机器是硬成本。
- ❌ 以为词级分词能「解决跨词假阳性」→ ✅ simple 无论字级还是 jieba，`京大` 仍会匹配 `北京大学`
  （倒排到字/子词）。词级只是切分不同，并非精确短语匹配。既然消不掉，就不值得为它花 200MB。

**`simple_query()` 的行为**：把查询按字拆开做 AND（召回导向）：
- `simple_query('内存')` → `"内" AND "存"`；
- 拼音 / 英文 → 前缀 OR 展开，`neicun`、`nc`（首字母）都能命中。

对个人博客这种「宁可多召回、不要漏」的场景非常合适。**故只启用 `simple_query`，不启用 jieba**。

## 3. 后端设计（`backend/app/core/search.py`）

### 3.1 索引：FTS5 外部内容表

```
CREATE VIRTUAL TABLE article_fts USING fts5(
  title, summary, content,
  content='articles', content_rowid='id',   -- 外部内容：不重复存正文，只建倒排
  tokenize='<simple|trigram>'
);
```

- **外部内容表**（`content='articles'`）：FTS 只存倒排索引，正文仍在 `articles` 表，省空间。
- **三个触发器**（AFTER INSERT / DELETE / UPDATE）把 `articles` 的变更同步进 `article_fts`
  （UPDATE/DELETE 用外部内容表的 `'delete'` 命令 + coalesce 写旧值）。数据天然一致，无同步管道。
- 排序：`bm25(article_fts, 10.0, 4.0, 1.0)` —— **标题权重 10 > 摘要 4 > 正文 1**，标题命中排前。

### 3.2 分词器：启动时择优 + 逐级降级

搜索质量取决于扩展是否装上，但**绝不能因为扩展缺失就启动失败**。故：

```
simple 扩展可用 ?  → 用 simple
     否则 trigram 可用 ? → 用 trigram
          否则           → LIKE 兜底（title/summary/content LIKE，按时间排序）
```

- `ensure_article_fts(engine)` 在 **lifespan 启动时**调用：探测 `simple_query` 是否可用，
  选定目标分词器；若现有 `article_fts` 的分词器与目标不符，**drop 索引 + 触发器后重建再 rebuild**
  （索引是可重建的派生数据，无损）。
- 每次查询按**库里实际的分词器**分派（读 `sqlite_master` 的建表 SQL 判断），不依赖脆弱的模块级全局，
  测试夹具切换也不会错乱。
- 查询期任何 `OperationalError` → `rollback` → 退回 LIKE，保证有结果。

### 3.3 扩展加载（`app/database.py`）

- 对每条 SQLite 连接的 `connect` 事件 `enable_load_extension(True)` + `load_extension(path)`。
- 路径默认 `backend/extensions/libsimple`（**不带后缀**，SQLite 按平台自动补 `.so`/`.dylib`/`.dll`，
  一个配置值跨平台通用）；可用 `.env` 的 `SIMPLE_EXTENSION_PATH` 覆盖。
- 缺文件 / 环境不支持 `load_extension` → **静默跳过**（try/except pass），退回 trigram。

### 3.4 高亮（XSS 安全）

服务端算好 `title_html` / `excerpt_html` 返回给前端 `v-html`：**先 HTML 转义**整段文本，
**再**用单趟正则把命中词包成 `<mark>`。摘要取首个命中词前后约 150 字的窗口。转义在先 → 用户内容
里的 `<script>` 不会被当标签，安全。

### 3.5 API

`GET /api/search?q=&page=&size=`（公开，只搜 `published=1`，size≤50）
→ `{total, page, size, query, items[]}`，item 含 `id/title/slug/created_at/word_count/tags[]/title_html/excerpt_html`。

## 4. 前端设计

### 4.1 ⌘K 命令面板（`components/SearchModal.vue`）

对齐 VitePress / Docusaurus / Algolia DocSearch 的主流形态：

- 顶栏是**触发按钮**（显示 `⌘K`/`Ctrl+K`）；按快捷键或点击从任意前台页弹出居中浮层。
- **输入即搜**：200ms 防抖 + 请求序号丢弃过期响应；面板只取前 6 条。
- 键盘：`↑↓` 选择（选中项 `scrollIntoView`）、`↵` 打开、`Esc` 关闭；鼠标悬停同步选中。
- **最近搜索**存 localStorage（`dawnop:recent-search`，去重、最多 6 条）；空态给拼音提示。
- 空 / 无结果 / 加载态齐全；移动端近全屏；打开时锁 body 滚动。

### 4.2 `/search` 整页（`views/SearchView.vue`）

保留作「查看全部 N 条」的**深链落点**（可分享、前进后退）。面板底部「查看全部」跳到这里。
这正是 VitePress 的组织方式：面板管快速跳转，整页管完整浏览。

## 5. 部署（务必照做）

- simple 共享库是**平台相关二进制，不入库**（`.gitignore` 排除 `backend/extensions/libsimple.*`）。
- 获取：`python backend/scripts/fetch_simple_ext.py`（按平台下载 v0.7.1 预编译库，**只取共享库不取词典**）。
- ⚠️ **生产服务器直连 GitHub Releases 会卡死，脚本在服务器上跑不通**。正确做法：
  1. 在能访问 GitHub 的机器下载 `libsimple-linux-ubuntu-22.04.zip`，取出里面的 `libsimple.so`；
  2. `scp` 到服务器 `/opt/dawnop/backend/extensions/libsimple.so`；
  3. 验证：`.venv/bin/python -c` 里 `load_extension` 后 `SELECT simple_query('内存')` 应得 `"内" AND "存"`。
- **rsync 后端务必 `--exclude='extensions/libsimple.*'`**（本地是 mac dylib，不能覆盖服务器的 linux so），
  同时排除 `.env` / `.env.*` / `*.db`。
- 重启后端后会自动把 `article_fts` 以 simple 重建（FastAPI 走 lifespan；Dawn 后端同理）。
  M6 之后生产是 `dawnop-dawn`（`systemctl restart dawnop-dawn`），它经 `DAWN_SIMPLE_EXT`
  指向同一个 `/opt/dawnop/backend/extensions/libsimple.so`。前端只需 rsync `dist`。
- 换机 / 重装：重复上面「手动下载 + scp」即可。生产 Python（Ubuntu 22.04 标准 python3.10）已确认允许
  `load_extension`；个别禁用该功能的构建会自动退回 trigram（不报错，但 2 字中文 / 拼音失效）。
