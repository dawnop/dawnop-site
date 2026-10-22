# 后端白盒测试根

`core/` 与 `jvm/` 按生产模块的相对路径保存测试块、测试专用 import 和夹具。
原有 299 项本站单测全部保留：core 71 项，JVM 228 项。依赖包测试合计后仍为 433 项。

从仓库根执行 `python3 backend-dawn/scripts/test_project.py`，或直接执行原有
`./backend-dawn/build.sh`。脚本创建一次性 core/app 镜像，合并同路径的选择 import，
把片段接回原模块，再运行 Dawn 自己的 test 命令。因此私有函数的白盒测试不需要
扩大生产 API，也不会把测试专用 Java 依赖放回 core 的生产根。

九组变异 harness 用同一脚本的 `--stage <空目录>`，生成带真实测试的项目后再变异、
构建与执行原矩阵。`source_path` 只负责按原模块名在 app/core 中定位锚点。
生产 `dawn build app/jvm` 直接消费原始源码和路径依赖，不读取本目录或测试镜像。
