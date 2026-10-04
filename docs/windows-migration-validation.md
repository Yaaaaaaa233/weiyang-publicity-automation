# Windows 与跨设备交接验证

日期：2026-10-03。使用者提供的 Windows x86_64 电脑，Docker 运行 Linux 容器。代码基线为 `a44ab2301deaafb06b4c3c0a792db8cad333459f`。Mac 通过 UU 远程监督操作，Windows 上的 DeepSeek Harness 执行仓库命令。该基线之后的业务更新不属于此次 Windows 验收版本。

## 已通过

| 项目 | 实际结果 |
| --- | --- |
| 环境与部署 | 仓库可用；Docker Engine 和 Compose 可运行；浏览器镜像成功构建，容器 healthy，本机入口绑定 `127.0.0.1:7900` |
| 浏览器首次自测 | `self-test` 退出码 0、`passed=true`；Mac 取回并查看截图，中文输入、坐标点击结果和 localStorage 标记正确 |
| 容器重启 | restart、重新 start 和 `self-test --resume` 均退出码 0，`passed=true`、`restored=true`；新页面的输入和点击状态重置，先前 localStorage 标记仍在 |
| Mac → Windows 任务交接 | 合成任务及两份证据导入 Windows 的独立测试账本；另一执行者领取、写入检查点、释放租约并导出 |
| Windows 账本检查 | 完成记录版本 10，10 条历史连续，租约为空；重复导入被拒绝，导入另一独立 Windows 测试库成功 |
| Windows → Mac 返回核对 | UU 文件传输取回证据；6 项检查点证据及两张截图校验值一致，原始 Mac 合成文件和前 4 条任务历史不变；返回记录导入独立 Mac 账本后任务及完整历史相同 |

`restored=true` 在本轮表示浏览器本机存储跨容器重启保留，不表示先前页面交互状态或账号登录状态已恢复。Mac 与 Windows 分别使用匹配设备架构的镜像，没有把 Mac 的 ARM 镜像直接复制给 Windows 使用。

## 实际执行条件

Windows 的 DSH 受限会话无法访问 Docker 命名管道；本轮 Docker 服务相关命令使用经核对的单次更宽工具权限。正常部署需要执行程序能够访问本机 Docker 服务，不能把受限工具会话直接当作完整运行环境。没有永久关闭工具审批或向公网暴露 Docker 服务。

DSH 记录了从受限会话启动 Docker Desktop 的失败，以及在更宽会话中成功启动的结果；完整电脑重启后的无人值守启动尚未验证。Docker Desktop 的可选登录页已跳过，未登录 Docker 账号。

UU 远程鼠标点击和剪贴板传递在本轮有不稳定表现，审批改用键盘确认。粘贴错入的草稿在发送前已清空；收尾指令未可靠发送，最终使用 UU 自带文件传输直接接收已生成的验收证据。远程 GUI 可用于监督，稳定业务动作仍通过仓库浏览器工具执行。

## 数据与接续

本轮只使用合成网页和合成任务，未操作秀米真实稿件、转移浏览器 profile、配置调度、登录秀米或发布内容。Windows 的报告和证据保持原样，公开仓库只记录脱敏结论。

- 本机协调状态：`local/windows-migration/state.json`。
- 返回证据：`backups/windows-return-20261003/artifacts/`。
- Windows 最终交接：返回目录中的 `windows-migration-return-handoff-round2.json`。
- Mac 返回审核：`local/windows-migration/mac-return-audit.json`。
- Mac 独立导入库：`backups/windows-return-20261003/data/mac-return-validation.sqlite`。

最终任务以 Windows 导出的版本 10 为准。主 Mac 账本中的版本 4 是旧的迁出记录，不能从该旧记录重新领取并继续写入；此次使用独立账本验证返回导入，没有覆盖旧任务或合并两份事件历史。

## 尚未验证

真实稿件的跨设备中途接续、完整基础格式整理保存、登录状态跨设备迁移、整台电脑重启后的无人值守恢复、独立模型执行循环、调度和 BOT。不同 agent 已实际使用相同命令与合成任务记录，但不能据此判定所有模型或工具适配都兼容。

下一轮在完整单组业务流程通过后，停止旧执行者并释放任务，将真实任务检查点及必要文件迁移到另一设备，由新执行者观察平台后接续。登录如需恢复，由使用者在专用浏览器完成。
