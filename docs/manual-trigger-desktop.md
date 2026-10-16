# 企业微信手动触发与 DSH 桌面会话接续

更新：2026-10-15。使用者选择沿用 DSH 桌面端登录，不配置独立 DeepSeek API Key。实现使用 DSH 原生插件服务，不模拟键盘、不导出桌面模型凭据。

## 当前验收状态

Mac 和 Windows 插件 0.2.3 的桌面连接段均已通过，业务开关保持关闭。Mac 验证本机入队到模型回执；Windows 验证交付的独立测试队列到指定标准模式会话、模型执行及 bridge_verified 回执。Windows 请求由 Mac 生成后传入，尚不能证明 Windows 本机入队入口或企业微信群入站通过。

Windows 已部署 d551894 快照及两份 SQLite 测试修复。Python 3.12 和 3.13 均运行 354 项：350 通过、4 项缺 Node 跳过、0 错误；Mac 354 项通过。Windows 专用会话实际权限为“工作区内修改”，Auto review 尚未验收；探针中的一次 PowerShell ACL 初始化错误由现有文件工具接续完成，未修改 ACL，无人工审批或救场消息。

真实企业微信群入站、业务多轮检查点接续及完整业务仍待验收。私有配置保持 desktop/bridge_test、business_executor_enabled=false，机器人、群和成员尚未绑定。不得把人工提示、本地探针或插件已加载当成群触发成功。详细证据见文末实测更新及忽略目录 local/windows-desktop-bridge-20261015。

## 运行链

授权成员在指定群发固定指令 → 官方企业微信 SDK 长连接接收 → Python 校验机器人、群、成员和命令 → SQLite 持久队列 → DSH 插件预留投递 → 恢复指定会话 → 投递固定任务 → 持久 inbox 回执 → agent 执行业务和检查点接续 → 用真实平台账本核对终态。

现有通知 Webhook 继续作为业务结果出口。它不是输入端；需要创建 API 模式、长连接的智能机器人。创建和管理通过团队允许登录的 Windows 企业微信客户端进行；Mac 接收器不需要 Mac 企业微信登录。首次群及成员 ID 的核对/绑定入口尚待真实平台配置，不可先开放任意成员来绕过。

## 固定指令与状态

- `连接测试`：只确认消息通道，不执行业务。
- `补跑今天`：按本次真实接收时间确定上海业务日期；只发送代码内固定提示词，不把群里的其他文字、引用、链接或附件交给 agent。
- `查询进度`：读取本地任务状态。区分排队、投递预留、DSH 接收、结果未知、连接测试完成、业务异常和业务完成；不能把收到指令或 agent 空闲当成功。

旧 `queue_only` 模式保留。迁移队列表时旧请求仍属于 queue_only，桌面 worker 不会自动认领。`desktop` 模式通过 `purpose` 区分：

- `bridge_test`：向指定 DSH 会话发送一次最小连接任务，读取 AGENTS.md 并写固定私有回执；不控制浏览器、不读凭据、不操作稿件或通知。
- `business`：调用既有完整流程，要求显式 `business_executor_enabled=true`。Mac 默认仍不启用真实业务，Windows 仍是正式业务节点。切换实际执行节点必须先停止旧节点并保留防重账本。

授权名单、purpose 或目标会话变更后，已排队请求不会升级为新权限：配置签名不一致会停止旧请求，必须由新的真实指令触发。

## 配置与部署

1. 先在 Windows 创建测试智能机器人，获取 Bot ID/Secret，并核对要绑定的真实群和运营成员 ID。凭据不发到聊天或写入 Git。Mac 仅在授权的私有配置中使用其 Secret。
2. 用 `fixtures/wecom-control.example.json` 建立 `local/wecom-control-test/config.json`，填写 Bot ID、机器人展示名、群和成员白名单。设置 `mode=desktop`、`purpose=bridge_test`，真实业务开关保持 false。
3. 在 Mac DSH 中准备一个项目专用会话，工作目录必须是本项目根目录，采用标准模式及使用者选择的 Auto review。把该会话真实 ID 填入 `desktop_session_id`。插件不创建替代会话，也不更改模型、账号、审批或权限。
4. DSH 桌面“插件 → 添加插件”选择本机 tgz。插件随包默认禁用；配置其 `projectRoot` 为项目绝对路径、`configPath` 为私有配置相对路径、`python` 为本机已有的解释器（Mac 通常 python3，Windows 通常 py）。配置核对后才启用该插件行及 `enabled`。
5. 启用后 `local/wecom-control-test/desktop.json` 应出现真实 mounted 回执；这只证明 worker 已加载，不代表模型执行成功。接收器运行在 `integrations/wecom-control`，以私有环境变量提供 `WECOM_CONTROL_SECRET`，执行 `node receiver.mjs`。当前未配置开机启动或任何新调度。
6. 同一机器人仅保持一台接收节点；本机 `receiver.lock` 也拒绝第二个进程。正常退出释放锁；异常遗留锁需先核对 PID 和真实进程，不自动删除未知锁。

所有 CLI 路径以项目根目录为基准；不在命令行传递 Secret。队列默认位于 `local/wecom-control-test/requests.sqlite`。receiver 与插件必须使用同一项目和配置，SQLite 通过事务原子认领。

### 团队找不到智能机器人入口时

腾讯官方指南说明：客户端入口位于“工作台 → 智能机器人”，找不到时先核对客户端版本；企业管理员可控制成员创建权限。需要请团队管理员核对“安全与管理 → 管理工具”中的智能机器人权限。仅凭成员未找到入口，不能宣称组织不支持，也不能把消息推送 Webhook 当作可收消息的替代接口。资料：https://intl.cloud.tencent.com/zh/document/product/1316/84454 、https://cloud.tencent.com/document/product/213/129174 。

桌面接续可以独立验收：先加载插件并把专用会话绑定在 `purpose=bridge_test`、`business_executor_enabled=false` 的私有配置，再从项目根目录执行：

```sh
python3 scripts/wecom_control.py --config local/wecom-control-test/config.json --op enqueue-test
```

这是本机操作员发起的最小探针，不需要 Bot ID、Secret 或空名单放行。队列保留 `origin=local_bridge_test`，只允许 bridge_test，不能升级或触发真实业务。其成功只能证明“本机入队 → DSH 会话接续”，不能证明企业微信群真实收发已经接通。Mac 本机探针与 Windows 桌面段已通过，范围见当前验收状态；真实群消息尚未验证。

## 接续与防重边界

`dispatch_reserved` 先于任何 DSH 投递持久化；只有 `sessions.flush()` 确认 inbox 持久后才记 `running`。投递或回执未知则保留 `outcome_unknown`，重启不自动另发、换会话或清记录。DSH 目标忙、队列未清空、目录不匹配或不存在时停止，不向其他会话寻找替代执行者。

插件使用原生 `sessionController.resolveAgent` 恢复指定会话，再使用 `followup` 和 `whenIdle`。已对照本机安装和内置包两种 resolveAgent 返回形式（直接 Agent / `{agent}`）做兼容回放。会话沿用自身模型、登录和权限；不转发其他会话的审批，不批准工具。

完整业务仍由 agent 使用 full_run 与受限修复接口执行。一次 agent 空闲时若业务仍是 agent_identity_required / agent_format_required，插件在同一 run 内投递固定接续指令；最多追加三次，无状态进展则停止。若调用结果未知，不用自动重试冒充接续。跨日请求过期，不在第二天冒充前一天已执行。

业务完成必须有同一 run/date 的实际 state、全部篇目的保存重开/正文图片/格式核验、同步 confirmed 账本和通知 accepted。未知同步、通知失败、只建立副本或只接收任务都不能标 completed。业务通知仍由现有 full_run / notify_wecom 负责，不把模型最后一段自由文本再当结果群发。

本轮修复了混合状态的身份入口：即使另一行已有转存记录，也能为新稿的语义行生成正文核验请求；仅收集相关行，不读空的已处理行。缺稿、转存预留等其他问题仍保留，不能因为身份已确认就跳过整组整理/同步的阻断。已有组合后追加头条的修订策略、候选收敛、未知同步窗口恢复与用量边界问题仍需后续处理，本补丁不清除既有提交记录。

## 真实验收顺序

先加载插件、绑定专用会话，用 bridge_test 验证一次模型接续和私有回执；再验证指定群的真实消息接收、重复消息只入队一次、任务忙时不并行及查询结果；最后在唯一业务节点启用 business，核对完整流程及企业微信通知。第一步桌面段已在 Mac 和 Windows 通过，具体范围见当前验收状态；后两步尚未通过。

源码依据：企业微信官方 SDK https://github.com/WecomTeam/aibot-node-sdk；DSH Agent/Session 原生接口 https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/core.zh.md。实现另核对了本机应用内置源码，不依赖从桌面进程提取密钥或未经文档说明的远程 HTTP 接口。

## 配置表单修复（0.2.1）

真实详情页已确认 0.2.0 安装且外层组件运行，但内部 enabled 默认 false，未投递任务。第一版 Config 字段未标 volatile，DSH 内置 settings 的 volatileForm 因而不生成表单；这是第一版桌面集成遗漏。0.2.1 将四项字段标为实时配置，并在每轮认领前读取当前值；关闭时不读取私有配置、不认领队列，配置未完成或路径越界时停止。已增加两项 worker 场景，共 11 个 Node 场景通过。修复包已生成，尚未安装，原生配置表单和真实接续仍待验收。外层显示“运行中”不能替代 mounted 回执或模型任务成功证据。

0.2.1 已由使用者安装，真实详情页显示版本；独立加载已安装模块核对四项 schema 均含 volatile。桌面仍未出现表单。核对内置插件管理实现，更新已有同名包返回 restart-required，切换组件不等于更新模块；现等待重启 DSH 后再验收表单。未绑定会话、未入队、未执行真实 agent。

### 重启后的修正：需要浏览器伴随组件（0.2.2，待安装）

使用者重启后 0.2.1 仍无配置入口，因此不能将缺表单完整归因为模块缓存。核对内置 PluginManagerPage：组件配置按钮由 plugins.row.config 的注册表控制，volatile 后端字段不会自行注册此页面。0.2.2 增加根入口的空 host 与 dsh.client 浏览器伴随组件，用原生 SettingsFormModel/SettingsValueField 保存四项字段，并按 `weiyang-publicity-control#weiyang-publicity-control` 注册配置入口；enabled 只接受 true/false，默认关闭。离线新增页面注册、四字段和布尔转换检查，Node 共 12 场景、Python 353 项通过。已安装模块还在只读的隔离 Cordis 上证明 schema 与 Volatile 引用可生成；这不等于真实页面/agent 验收。新包待安装及重启后核对，尚未绑定会话或运行探针。

0.2.2 安装重启后，真实页面已出现“配置 weiyang-publicity-control/desktop-plugin”按钮；进入后四字段与默认 enabled=false 均核对通过。工具 paste/setValue 未改变空的 projectRoot，故保存/会话绑定仍待完成。未运行真实模型探针；不能把配置入口通过称为接续通过。

后续在使用者停止操作电脑后重试：项目路径已保存、项目工作区可见；但原生 AX 与截图对 P&E 弹层状态不一致，置前、重新绑定及重置 CUA 会话后仍未恢复稳定控制，坐标操作出现 noWindowsAvailable。故不能认定是使用者干扰或全屏所致。标准模式、真实专用会话 ID、启用及最小探针尚未完成；私有策略仍 queue_only，业务未启用。

## 2026-10-15 首次真实接续与 v4 来源修复

标准模式专用会话已初始化并回复“准备完成”；通过该会话的原生 Session 导出地址核对真实 ID，并核对项目存储头 cwd。私有配置设为 desktop/bridge_test、business_executor_enabled=false，插件真实 mounted。首个 local_bridge_test 入队后 journal 为 outcome_unknown，目标会话显示 `format v4 message requires a producer-owned source kind`，未获得模型测试回执。失败 run 与来源记录保留，不清队列或直接重发。

根因：适配器还使用已被 v4 拒绝的 kind=plugin 包装。0.2.3 改为 producer-owned kind=weiyang-publicity-control，保留真实 origin/run_id，测试说明不再把本地探针写成群成员指令。使用当前应用内置 createUserMessage 与 assertV4RowAdmission 独立验证：旧值拒绝、新值通过；Python 353 项、Node 12 场景通过。新包待安装，真实接续仍未通过。DSH 另有使用者 PPT 任务运行，不为更新强行重启。后续先处理失败会话及 unknown 的真实持久状态，修复加载后才能安排新探针；不把本次当作群触发或业务验收。

## 2026-10-15 本地真实接续通过

0.2.3 已经通过原生 GUI 安装并重启加载。旧探针重新观察后确认原生会话仅保留初始化一轮，无被拒绝任务的模型执行或回执；新增 operator-only `resolve-probe-failure` 将其保留为 needs_attention，记录精确 v4 拒绝及核查证据，不重放。该入口只接受 business-disabled 的 local_bridge_test、相同配置签名和 run/session、fresh 5 分钟观察、未记录 message_id 的指定预模型拒绝；普通 mark 和业务未知结果仍不得用此出口清除。

新探针完成：本机入队 → worker 原子预留 → 指定标准模式会话收到执行请求 → 持久 inbox → 模型读取 AGENTS 并写 JSON → 回读 request/run/status → journal bridge_verified。原生界面显示该轮约 36 秒，6 步（包括之前初始化累计显示）；本轮无需额外审批或人工救场。真实工作目录、独立消息 ID 和回执在私有状态中核对。没有浏览器业务、转存、群通知、API Key 导出或调度变更；Windows 仍是业务执行节点。Python 354 项和 Node 12 场景通过。

此验收只覆盖 local_bridge_test 的桌面段，不能称为群触发通过，也不能称为完整业务或有检查点的多轮接续通过。真实机器人入口、群与成员绑定及入站消息仍未接通。后续先验证真实机器人权限/入口，保持 purpose=bridge_test；取得真实群消息后才验证整个输入链。

## 2026-10-15 Windows 桌面段通过

Windows GUI 已核对插件 0.2.3、项目路径及 py 解释器；真实标准模式会话的存储头与项目目录一致。业务关闭的 local_bridge_test 请求由操作员在 Mac 生成全新队列，确认 Windows 尚无队列后经 UU 传入，再由插件自动投递。模型读取规则并回写匹配 request/run 的回执，取回 SQLite 确认 bridge_verified、真实 message_id 及会话绑定。此步骤证明 Windows 队列到真实模型会话的桌面段，不证明 Windows 本机入队入口或企业微信群入站。

GUI 开启和部署有人工参与，测试投递后无人工审批或救场消息。会话实际权限显示“工作区内修改”，不是 Auto review；一次 PowerShell 工作区 ACL 初始化失败后，模型改用已有文件工具完成，未更改 ACL。Auto review、业务多轮检查点接续与完整转存仍待验收。私有配置保持 bridge_test、business_executor_enabled=false，机器人及群成员绑定为空；不以本地成功开启业务或接收任意群消息。私有回传及复核位于 local/windows-desktop-bridge-20261015。

## 2026-10-15 Windows 团队入口核查

在使用者打开的目标团队客户端中，侧栏实见消息、日程、待办、会议、微文档、微盘、通讯录、高级功能及我的企业，没有工作台。高级功能已打开，当前可见的是获客助手、外部联系人规模、邮件及文档等入口，未见智能机器人。客户端关于页版本为 5.0.12.6018；已点击检查更新，但未取得明确的最新版本或可更新回执，不能称已确认最新版。团队切换菜单显示当前选中的是带 # 图标的团队，未切换至其他组织。

这些观察证明当前账号界面缺少官方指南所述入口，尚不能单凭此判定所有团队均不支持，或认定修改管理员权限一定能解决。接下来需核对目标团队类型的官方支持范围及创建权限；在此之前真实群入站保持未验收，不创建替代企业、不变更组织、不把既有消息推送 Webhook 当成接收接口。桌面接续的既有通过结果继续有效。

### 补充：官方扫码创建路径

继续查证发现，缺少工作台不能作为不支持机器人接入的结论。腾讯云官方远程终端文档明确列出两种方式：扫码创建并授权机器人，或工作台手动创建（https://cloud.tencent.com/document/product/1759/132562）。企业微信官方开源 WecomTeam/wecom-cli 的 auth init 支持扫码创建绑定，并支持 --no-browser、--output-qrcode；配置默认加密保存，可通过 WECOM_CLI_CONFIG_DIR 隔离（https://github.com/WecomTeam/wecom-cli/blob/main/docs/cli-reference.md）。这条官方路径值得独立验证，不要求安装新的 DSH 第三方通道插件，也不能据此推断当前团队必然支持多人群触发。

当前仅确认官方存在扫码创建绑定机制；目标团队实际可选范围、授权权限、机器人能否入指定群、是否限制创建者及其他成员能否触发，均须真实授权页及收消息实测。不得根据群成员数推断企业人数或授权资格，不通过其他云平台代建并转交凭据。首次验证应隔离私有凭据、保持业务关闭，先展示授权范围，新增凭据/权限及协议由使用者在当次操作确认。现有通知 Webhook 不变。此次未安装 CLI、未扫码授权、未创建机器人。

### 扫码实际结果（2026-10-15，2026-10-17 整理）

Mac 私有目录安装官方 @wecom/cli 1.3.4，生成二维码后，使用者手机进入“请先创建智能机器人 / 一键创建智能机器人”页面。使用者报告点击后立即“参数不合法”，无错误码、未进入团队选择，并确认手机客户端已是最新版本。未获得创建成功或授权完成回执，不将原因认定为团队不支持，也不宣称扫码路径已接通。旧码超时记录与本轮日志保留私有目录；未将凭据接入接收器，业务保持关闭。后续需平台进一步诊断或替代触发入口，避免反复创建。
