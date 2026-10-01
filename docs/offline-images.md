# 减少换电脑时重复下载

镜像、代码与运行数据是三份不同的内容。Git 只传代码；浏览器命名卷另行备份。

在同一 CPU 架构的电脑之间，可以把已经构建的项目镜像导出为文件，再在另一台电脑导入，以减少大镜像下载。以下命令在当前仓库目录运行：

```sh
docker compose build
docker image save -o backups/browser-image.tar weiyang-publicity-browser:latest
```

把镜像文件放到目标电脑后：

```sh
docker image load -i backups/browser-image.tar
docker compose up -d --no-build --wait
```

目标电脑仍需本项目代码、本机 `.env` 和正确恢复的数据卷。未恢复数据卷时会得到一个新的独立浏览器。

Apple 芯片 Mac 是 ARM64，常见 Intel/AMD Windows 电脑是 AMD64。ARM64 镜像文件不能当作已验证的 AMD64 部署包；两种架构分别构建和验收，再分发对应镜像。不要为省下载而把默认部署强制设为跨架构模拟。

镜像本身不应包含登录数据或 `.env`。本项目的构建只复制工具代码和测试页面，运行数据放在独立卷中。导出的镜像属于本机备份文件，不提交到 Git。

本文件是操作说明；镜像导出导入和异机恢复尚待单独实测。
