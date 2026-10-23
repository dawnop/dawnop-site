# Native 安装下载代理与 helper 身份

状态：实现完成，本地下载与源码负控通过；完整 CI 验收见交付报告。

## 下载边界

沿用 JVM 安装器的 `/opt/dawnop-dawn/.deploy-proxy`、`DEPLOY_PROXY` 显式覆盖和空白清理。
入口与 Python helper 都先清理继承的六个代理环境变量，只在 GitHub 请求的 curl 参数中传代理。
文件缺失、内容为空或端口探测失败时警告并直连，不把代理地址写入报告或源码。
原 JVM 安装器保持原样；本地健康检查、engine-info 与 systemd 命令不带代理。

helper、GitHub API 和 artifact ZIP 均最多尝试三次，间隔 1、2 秒。每次保留 curl 原始错误，
另记录原始退出码；最终仍用最后的 curl 退出码失败。请求分别受 60 秒、600 秒上限约束，
artifact 另有连续 60 秒低于 1 KiB/s 的截止；重试覆盖目标文件，失败文件不会进入执行或解包。
Python 外层等待应大于 curl 自身截止，避免原来统一 120 秒等待误杀 600 秒下载。

## helper 执行前校验

raw URL 钉完整 40 位提交 SHA。受信任的入口内固定 helper 的 Git blob SHA，
计算 `SHA1("blob " + 字节数 + NUL + 文件字节)`，相符才执行 helper。
预期摘要不从代理下载，避免文件与摘要被一起替换。校验器由入口内联 Python 提供，
不加载尚未校验的 helper；正常 HTTPS 证书验证仍保持开启。
入口须从目标提交检出；旧提交的不同 helper 会拒绝，不能用最新入口任意安装旧 helper。
helper 修改必须同批更新入口的 blob pin，测试直接计算真实文件摘要防止遗漏。
随后 helper 继续校验 GitHub artifact ZIP SHA-256 和 manifest 完整文件集合/摘要。
撤回继续优先使用已安装的离线 helper。

## 验收

测试只用本地假 GitHub、假前向代理及隔离临时目录。真实 curl 经测试专用包装器改写 URL，
生产不提供 URL 或安装根覆盖入口。覆盖有代理、缺文件、代理不可达、继承变量清理、
三次重试、最终退出码、helper 摘要拒绝、API 与 ZIP 下载，以及健康检查不经过代理。
实际源码副本变异两项：导出全局 http_proxy、移除 helper 摘要拒绝；同一断言必须先红。
测试纳入现有 Python 3.10 pytest 收集，不增加 workflow 步骤或 job。

## 不做的（理由）

不改 JVM deploy.sh，不部署、不访问服务器、不切流、不改业务源码或 golden；
本任务只修 native 安装下载链路。语言仓门禁脚本在站点仓不存在，不跨仓执行或修改。
