# 首台开发机环境验证

验证日期：2026-10-01。设备：Apple 芯片 Mac，macOS 26.6.2，ARM64。

## 已完成

- 从 Docker 官方下载 Apple silicon 安装包，完整文件校验与磁盘镜像校验通过。
- macOS 应用签名检查通过，签名方为 Docker Inc，证书链为 Apple Developer ID。
- Docker Desktop 4.93.0 安装到 Applications；界面显示 Engine running。
- `docker version` 同时返回客户端及服务器版本 29.8.1，服务器为 Linux ARM64。
- Docker Compose 版本为 v5.5.1。
- 官方 `hello-world` ARM64 镜像下载并运行成功，输出 Hello from Docker，命令退出码为 0。
- 安装完成后已卸载安装磁盘镜像。

验证命令使用 Docker 应用中的 CLI，并为命令补充其工具目录到 PATH；自动化执行环境的 PATH 与用户终端可能不同。日常终端配置见[安装说明](setup.md)。

## 尚待验证

首次 Docker 验收时，后续待办包括专用浏览器、人工登录、测试稿件、恢复与跨系统迁移。其后的实际进展见[浏览器验证](browser-validation.md)、[稿件验证](article-validation.md)与[任务账本验证](tasks.md)。Windows 部署、真实跨 agent 接管及异机恢复仍待实测。

截图保存在本机 `local/`，由 Git 忽略。本阶段的 Docker 基础验证未接入业务账号或模型密钥。
