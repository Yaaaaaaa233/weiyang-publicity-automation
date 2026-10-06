# 未央宣传组运营自动化

在 Windows / macOS 电脑上通过 Docker 运行宣传运营工作流，结合规则检查、agent 与专用浏览器，辅助秀米稿件检查及公众号草稿操作。

## 当前状态

2026-10-05 新增三个脚本入口：WPS 共享脚本运行并直接取回日志、秀米全标签全页候选检索、统一读取/检查/通知/账本入口。Mac Docker 浏览器的完整读取链路已实测，统一入口准确保留头条及版本问题，未自动整理或同步；通知默认预览。用法、私有配置样例、接续及迁移限制见 [统一检查入口](docs/daily-runner.md)。Python 与账本仍在宿主机，本次更新尚未在 Windows 或连续无人值守验收。按使用者要求，本轮没有处理或调整定时任务。

最新调度调整（2026-10-04）：已按使用者要求将现有任务改至每天北京时间15:10；当天第一次计划触发使用9月21日历史预约测试，后续回到当天预约。历史测试仍须实时读取，缺稿提醒单独标注测试日期；2026-10-04 的 9-21 历史测试已触发并完成（三篇副本整理与保存重开核对通过），首次无人值守运行仍未验收，参见[每日运行](docs/daily-run.md)。

已配置当前 Codex 聊天每天北京时间 15:10 接续：读取当天预约，检查常用秀米账号收稿完整性；全部收齐后整理独立副本，缺稿、版本歧义或检查失败时提醒。新增只读收稿判断脚本及 17 项离线测试；企业微信单向提醒已完成手机端创建、脚本发送、群内核对及一次结果通知，通知脚本另有 10 项离线测试，全套 53 项通过。首次每日无人值守真实运行、Windows 定时入口和通知，以及外部 BOT 任务入口仍待验收或实现。迁移规则、执行说明及限制见[每日运行](docs/daily-run.md)。

目前包含架构说明、环境安装步骤、专用浏览器配置和跨系统操作命令。首台 Apple 芯片 Mac 已通过 Docker、浏览器输入/点击/截图、会话复用及重启后的数据保留验证，见[环境验证记录](docs/environment-validation.md)与[浏览器验证记录](docs/browser-validation.md)。秀米合成测试稿已完成输入、读取、保存、预览、图片模板保存后刷新核对及容器重启后找回验证，见[基础稿件验证](docs/article-validation.md)。已有按页面区域读取内容、生成本机检查报告，以及 SQLite 任务记录、领取和 JSON 交接命令。合成测试稿已通过日常接收账号的跨账号转存、独立副本找回及刷新后内容核对，见[转存验证](docs/transfer-validation.md)。当前日常范围为接收稿件、依据每日推送安排表确定篇目与顺序、整理基础格式，见[每日推送验收方案](docs/daily-push-validation.md)。历史四篇公众号转存已获使用者内容验收；真实整组基础格式自动修复、独立 agent 执行循环和 Docker workflow 服务仍待验证。

2026-10-03 已完成四篇真实历史稿件的候选匹配：两篇明确候选，另两篇分别有周次和版本歧义，见[稿件匹配验证](docs/draft-matching-validation.md)。其中一篇已完成副本创建、测试标题保存、稿件库找回及刷新后的内容核对，见[真实副本验证](docs/real-copy-validation.md)。已有格式检查通过；另在独立合成稿中完成了人工监督下的分隔符修正、尾部空组件删除及保存重开验证，见[格式修正验证](docs/format-correction-validation.md)。真实错误稿件修正、自动批处理及整组整理仍待验证。

同日 Windows x86_64 基础部署、浏览器自测与容器重启保留通过；合成任务由 Mac 迁出、Windows DSH 接续，再返回 Mac 的独立账本验证一致。见 [Windows 与交接验证](docs/windows-migration-validation.md)。此结果覆盖 `a44ab23` 基线；该基线之后的业务更新、真实稿件和登录状态迁移仍待 Windows 验收。

2026-10-04 已在“我的图库 → 头尾图”找到账号头尾 GIF，并在已有合成稿完成补头图、替换测试尾图占位、头图零间距与保存重开检查；正文及内文空行保留。见[头尾图验证](docs/head-tail-validation.md)。真实测试副本已含首尾图，未重复添加；自动批处理和复杂组件仍待验证。

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
- [Windows 与跨设备交接验证](docs/windows-migration-validation.md)
- [专用浏览器启动与操作](docs/browser.md)
- [首篇稿件验证清单](docs/first-article-check.md)
- [每日推送基础整理验收](docs/daily-push-validation.md)
- [每日预约、收稿检查与定时入口](docs/daily-run.md)
- [WPS、秀米与统一检查脚本](docs/daily-runner.md)
- [企业微信群异常提醒](docs/wecom-notifications.md)
- [预约表只读核对记录](docs/reservation-reading-validation.md)
- [已接收稿件匹配验证](docs/draft-matching-validation.md)
- [真实稿件副本创建与保存验证](docs/real-copy-validation.md)
- [合成稿基础格式修正验证](docs/format-correction-validation.md)
- [图库头尾图补齐验证](docs/head-tail-validation.md)
- [秀米多图文同步到公众号验证](docs/wechat-sync-validation.md)
- [同步组合脚本与断点接续](docs/sync-workflow.md)
- [任务账本与交接](docs/tasks.md)
- [跨账号转存验证进度](docs/transfer-validation.md)
- [离线镜像分发说明](docs/offline-images.md)

历史四篇转存已获使用者内容验收。新增同步工作流按确认过的计划自动组合稿件、检查并记录交接；实际 Mac 自动组合已通过，提交命令与 Windows 新脚本仍待实际验收。已有提交次数不会因改运行目录而归零。用法及限制见上述说明。

## 版本与数据

代码、业务规则、配置样例和安装说明进入 Git。真实稿件、运行数据库、浏览器会话、报告和密钥保存在本机，独立备份。

计划支持 `linux/amd64` 与 `linux/arm64`。Windows 和 macOS 均使用 Linux 容器；不同架构必须实际构建并验证。

### WPS 共享读取入口

2026-10-05 已切换到文档共享脚本，个人版冻结保留，后续业务与测试仅用共享版。共享入口只核对源码并运行，不反复覆盖代码；跨账号与无人值守仍待验证，历史日期网页参数入口尚未配置。见 [WPS 网页读取流程](docs/wps-browser-reader.md)。
