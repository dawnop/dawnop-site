# 中文分词扩展（wangfenjin/simple）

后端搜索优先使用 [wangfenjin/simple](https://github.com/wangfenjin/simple) 这个 SQLite
FTS5 分词扩展（字级 + 拼音，**不启用 jieba**，故几乎零内存开销）。共享库本体是**平台相关的
二进制，不入库**，各环境自行下载到本目录。

## 获取

在 `backend/` 下运行（自动按当前系统/架构下载对应预编译库）：

```bash
python scripts/fetch_simple_ext.py
```

会得到 `extensions/libsimple.so`（Linux）或 `libsimple.dylib`（macOS）。

## 加载与降级

- 后端启动时对每条 SQLite 连接自动 `load_extension`（见 `app/database.py`）。
- 路径默认取本目录的 `libsimple`（不带后缀，SQLite 按平台自动补）；也可用
  `.env` 的 `SIMPLE_EXTENSION_PATH` 覆盖。
- **缺文件或环境不支持时静默跳过**：搜索自动退回内置 `trigram`，再不行退回 `LIKE`，
  功能不受影响，只是中文检索质量下降。

## 说明

- 只用 `simple_query()`（字级 + 拼音）；`jieba_query()` 需要额外 ~13MB 词典且常驻
  ~200MB 内存，本项目**不使用**，故无需下载 `dict/` 目录。
- 生产（Ubuntu 22.04 x86-64）部署后在服务器上跑一次上面的脚本即可。
