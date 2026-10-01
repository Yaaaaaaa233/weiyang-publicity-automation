# 开发环境安装

## macOS

1. 从 [Docker 官方 Mac 安装页](https://docs.docker.com/desktop/setup/install/mac-install/)下载符合芯片架构的 Docker Desktop。Apple 芯片选择 Apple silicon。
2. 打开 DMG，将 Docker.app 安装到 Applications 后启动。
3. 使用者阅读并接受服务协议。涉及系统密码或权限时，由设备使用者确认；不需要把密码发送到聊天中。
4. 等待 Docker Engine 启动。新版本默认采用用户级配置；实际步骤以安装版本显示为准。无需为了本项目预先安装 Rosetta。
5. 打开新终端执行下方验证命令。

## Windows

1. 从 [Docker 官方 Windows 安装页](https://docs.docker.com/desktop/setup/install/windows-install/)下载符合设备架构的 Docker Desktop。
2. 按安装器要求配置 WSL 2 与虚拟化，必要时重启。
3. 启动 Docker Desktop，使用 Linux 容器。使用者确认协议及必要的系统授权。
4. 在 PowerShell 中执行下方验证命令。

## 验证

```sh
docker version
docker compose version
docker run --rm hello-world
```

`docker version` 应同时显示客户端与服务器信息；只有客户端版本不代表 Engine 已启动。`hello-world` 成功代表基础容器运行可用，不代表浏览器或业务工作流已完成。

如果终端找不到命令，先重开终端，并检查 Docker Desktop 安装和 CLI 路径。

macOS 用户级安装的 CLI 通常位于 `$HOME/.docker/bin`。如果重开终端后仍找不到 `docker` 或 `docker-credential-desktop`，可在当前终端执行以下命令后重试；它只影响当前终端：

```sh
export PATH="$HOME/.docker/bin:$PATH"
```

## 下一步

Docker 基础验证通过后，按[专用浏览器说明](browser.md)初始化本机配置并启动浏览器。

业务任务服务、独立 agent/模型适配、聊天平台 BOT 和定时计划尚未实现。
