# 本地任务账本与交接

当前有一个 Python 标准库实现的 SQLite 任务账本，默认数据库为 `data/tasks.sqlite`。需要宿主机 Python 3.9 或以上；Windows 将以下命令的 `python3` 改为 `py`。

它保存来源、状态、当前步骤、证据路径、待办、版本及事件历史。它尚未接入独立模型执行循环或 BOT，也未作为独立 Docker `workflow` 服务运行。当前 Codex 聊天的每日定时接续要求 agent 用本账本保存检查点，见 `docs/daily-run.md`；首次无人值守调用仍待验收。

新增 `scripts/sync_workflow.py` 将已确认计划到秀米组合的步骤接入本账本，按账号/日期关联任务，并在点击同步前记录不可自动重放的提交意图；每次结束释放租约并导出交接。该程序是确定步骤的宿主机脚本，不是独立模型循环。用法及实际覆盖见[同步组合脚本](sync-workflow.md)。

## 手动运行

```sh
python3 scripts/taskctl.py create local/task-source.json
python3 scripts/taskctl.py list
python3 scripts/taskctl.py claim TASK_ID EXECUTOR_NAME --ttl 900
python3 scripts/taskctl.py checkpoint TASK_ID EXECUTOR_NAME local/checkpoint.json --version CURRENT_VERSION
python3 scripts/taskctl.py release TASK_ID EXECUTOR_NAME
python3 scripts/taskctl.py export TASK_ID task-handoff.json
```

`TASK_ID` 与 `CURRENT_VERSION` 使用上一步命令返回值。执行者名称在同一次工作中保持一致。领取、更新和释放都会增加版本号；账本只允许一个执行者持有未过期租约，领取时间范围为 60–3600 秒。用同一执行者重新 `claim` 可以延长租约，但会增加版本；过期后先重新观察实际平台状态，再领取和接续。

来源文件为 JSON 对象，内容由实际业务提供，不在公开仓库保存稿件。检查点样例：

```json
{
  "status": "verification_pending",
  "step": "save_requested",
  "next_step": "重新打开平台稿件，核对是否保存成功，再决定后续操作。",
  "evidence_paths": ["artifacts/before-save.png"]
}
```

状态可选：`received`、`checking`、`needs_manual`、`verification_pending`、`ready_for_review`、`completed`、`failed`。步骤使用简短标识，稿件内容保存在私有来源或检查点文件中。任务状态不等于平台发布状态。

命令不会替使用者执行浏览器动作或自动判断动作成功。先观察结果，再记录真实检查点；有副作用的动作结果不确定时保留 `verification_pending`，不直接重复保存或创建。

证据必须是 `artifacts/`、`local/` 或 `data/` 下现存文件的项目相对路径，不能含 `..` 或指向项目之外。记录检查点时保存证据的 SHA256；读取或交接时检查文件是否变化。每个版本使用新的证据文件名，不覆盖旧版本。更新前检查租约及预期版本，避免旧执行者覆盖新记录。

## 读取与换 agent

```sh
python3 scripts/taskctl.py show TASK_ID current-task.json
```

命令输出任务 ID、状态、步骤、版本和文件路径；完整记录写入 `artifacts/current-task.json`，不在终端打印稿件来源或内容。

旧执行者停止操作、完成最后检查点并释放租约后，新执行者读取记录及证据，观察浏览器，领取任务，再继续。`show` 可以读取正在执行的任务，但不能作为可导入的交接记录；`export` 在仍有有效租约时拒绝运行。

租约目前只保护账本领取与更新；浏览器工具还未强制携带任务身份。不要同时运行两个业务执行者，也不要把账本租约当作浏览器全局锁。不同 agent 的工具接入仍需实际验证。

## 换电脑或导入独立账本

```sh
python3 scripts/taskctl.py import artifacts/task-handoff.json
```

交接 JSON 包含任务和完整事件历史，不包含浏览器登录信息或证据文件本体。先停止旧节点写入并释放任务，再导出；迁移时把引用的证据按原项目相对路径一并复制。新节点安装环境、恢复文件并登录平台后导入。导入会检查当前证据存在且 SHA256 一致、历史序列完整及无活跃租约；同一任务 ID 已存在时拒绝覆盖。

同一台设备切换执行者无需导入，直接使用同一数据库即可。异机时 JSON 导入是当前工具支持的路径；直接复制运行中的 SQLite 文件、开着两个节点继续写入，都不属于已验证迁移流程。命令本身不会停止旧机器上的浏览器或调度。

## 本机验收记录

日期：2026-10-01，Apple 芯片 Mac。将已完成图片预览检查的合成任务录入主账本，状态记录为 `needs_manual`，下一步为真实稿件、账号及业务规则验收。

已通过：独立命令进程间的状态保留、执行者冲突拒绝、旧版本拒绝、错误执行者释放拒绝、未释放任务导出拒绝、释放后导出、独立测试数据库导入后任务与完整历史一致、重复导入拒绝、证据 SHA256 不一致或缺失时拒绝、历史缺失时拒绝。

本机数据：`data/tasks.sqlite`；测试导入库：`data/import-validation.sqlite`；交接：`artifacts/task-handoff.json`。这些文件均由 Git 忽略。

这是同一台 Mac 上的迁移模拟，没有实际换 agent 或 Windows 设备，没有创建业务调度，也没有向聊天平台发送消息。

## 跨设备补充验收

2026-10-03，合成任务从 Mac 导出，在 Windows 的 DeepSeek Harness 中导入、接续并导出完成记录。任务和证据经 UU 文件传输返回后，Mac 校验 6 项检查点证据、原始事件及合成文件，再导入独立 Mac 账本，任务和完整历史一致。实际换设备及执行者的合成交接已通过，详见 [Windows 迁移验证](windows-migration-validation.md)。不覆盖原 Mac 的旧版本记录，也不把这次结果写成真实稿件或登录状态迁移通过。
