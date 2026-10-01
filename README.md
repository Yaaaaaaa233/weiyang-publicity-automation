# 未央宣传组运营自动化

在 Windows / macOS 电脑上通过 Docker 运行宣传运营工作流，结合规则检查、agent 与专用浏览器，辅助秀米稿件检查及公众号草稿操作。

## 当前状态

目前包含架构说明、环境安装步骤、专用浏览器配置和跨系统操作命令。首台 Apple 芯片 Mac 已通过 Docker、浏览器输入/点击/截图、会话复用及重启后的数据保留验证，见[环境验证记录](docs/environment-validation.md)与[浏览器验证记录](docs/browser-validation.md)。秀米合成测试稿已完成输入、读取、保存、预览、图片模板保存后刷新核对及容器重启后找回验证，见[基础稿件验证](docs/article-validation.md)。已有按页面区域读取内容、生成本机检查报告，以及 SQLite 任务记录、领取和 JSON 交接命令。正式转存、任意复杂稿件、独立 agent 执行循环、Docker workflow 服务和 Windows 本机部署尚待验证。

## 开发顺序

1. 安装 Docker Desktop，验证 Docker Engine 与 Docker Compose。
2. 搭建专用浏览器容器，验证观察、点击和人工登录。
3. 手动处理一篇测试稿件，保存检查报告及任务状态。
4. 实现中断恢复和草稿结果验证。
5. 分别在 Windows x86 与 Apple 芯片 Mac 上验证部署、重启和数据恢复。
6. 加入定时调度与选定聊天平台的 BOT 入口。

最终发布由人工完成。

## 环境与架构

- [环境安装](docs/setup.md)
- [架构与迁移](docs/architecture.md)
- [专用浏览器启动与操作](docs/browser.md)
- [首篇稿件验证清单](docs/first-article-check.md)
- [任务账本与交接](docs/tasks.md)
- [离线镜像分发说明](docs/offline-images.md)

## 版本与数据

代码、业务规则、配置样例和安装说明进入 Git。真实稿件、运行数据库、浏览器会话、报告和密钥保存在本机，独立备份。

计划支持 `linux/amd64` 与 `linux/arm64`。Windows 和 macOS 均使用 Linux 容器；不同架构必须实际构建并验证。
