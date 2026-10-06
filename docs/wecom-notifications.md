# 企业微信群结果通知

## 最新业务规则（2026-10-05）

重点通知四类：WPS登录异常、秀米登录异常、收稿完整性及非阻断标题差异、格式验收与公众号转存后的结果。无预约仍按既有要求报告一次本轮结果。运行ID固定用于同次接续，新的一次真实执行使用新的ID，不能为绕过去重任意更换。

标题不同但通过部门、日期、主题、正文、周次及唯一版本等真实证据确认同稿时，在候选写`identity_confirmed=true`与`identity_reason`。`daily_check.py`保留两种原始标题，产生`warnings.title_difference_matched`；只提醒人工注意，不等待人工核验，不阻止后续整理或已授权转存。仅标题相似、错误周次、同题版本未明确仍不能放行。无需再次获得人工确认，但必须保留实际核验依据。

登录失效分别记录`reservation.status=login_required`、`xiumi.status=login_required`，生成不同提醒。其他网络或读取失败不伪装成登录失效。

完成消息按头条第一、其余源表行序列出预约原始标题，在头条后加`（头条）`；列出全部标题差异、秀米实际标题与同稿判断依据。全部格式验收及明确转存结果通过后才写“已校验基础格式并转存到微信公众号草稿箱”；仅提交则写“转存结果待确认”。正文核对、封面或同步结果未验收，不能因收齐或按钮点击成功报完成。当前每日任务的同步授权范围不因通知格式调整而自动扩大；同步仍由独立工作流执行，公众号后台由使用者核验。

结果生成入口（默认预检，不发送；发送追加`--execute`）：

```sh
python3 scripts/notify_wecom.py --report artifacts/daily-check-DATE-RUN.json \
  --observation local/daily-check/DATE/RUN.json \
  --completion local/daily-check/DATE/RUN-completion.json
```

completion必须是同日期、同run_id的真实检查点摘要，字段示例如下，所有值与路径均为虚构占位，不构成验收记录：

```json
{
  "date": "2026-10-05",
  "run_id": "fictional-run",
  "formatted_articles": [
    {
      "row": 7,
      "source_id": "observed-source-id",
      "draft_id": "observed-independent-copy-id",
      "saved_reopened": true,
      "body_images_verified": true,
      "format_verified": true,
      "evidence_paths": ["local/fictional-format-check.json"]
    }
  ],
  "sync": {
    "date": "2026-10-05",
    "status": "confirmed",
    "confirmed_by": "user",
    "ordered_draft_ids": ["observed-independent-copy-id"],
    "evidence_paths": ["local/fictional-sync-confirmation.json"]
  }
}
```

formatted_articles需覆盖全部预约行；source_id须与已匹配候选一致，draft_id不得重复。同步列表须与头条/行序对应的副本完全一致。sync.status接受`submitted`或`confirmed`，后者confirmed_by仅接受有实际证据的`user`或`platform`。生成器校验字段、映射、标题差异和证据文件存在；证据的实际内容仍需执行者核对，不能把手填true或创建空文件作为真实验收。未完成或中断的部分结果仍用核对后的私有message-file如实报告。

最新修改通过96项Python测试；本轮未发送真实消息。

新增宿主机脚本 `scripts/notify_wecom.py`，Python 3.9+ 标准库实现；Windows 将 `python3` 换为 `py`。实际群和 Webhook 放本机私有配置，公开仓库不记录接收群名、凭据、真实预约或消息正文。

本机 iPhone 镜像已实际找到内部群的「聊天信息 → 消息推送 → 添加 → 添加自定义消息推送」表单。镜像直接中文输入及粘贴未生效，原生按键能输入英文名称。创建和实际发送状态另记在本机 `local/wecom-notifications/`；未完成保存和回执、群内核对前不能记为已接通。

## 配置和运行

将创建者取得的完整地址保存到本机 `.env` 的 `WECOM_WEBHOOK_URL`，保留已有浏览器配置。环境变量同名设置优先。配置只接受企业微信官方 HTTPS 群消息推送地址，不跟随重定向，不在输出、回执或异常中打印完整地址。

使用已检查的私有文字文件，默认只预检，第二个命令才发送：

```sh
python3 scripts/notify_wecom.py --message-file local/wecom-notifications/message.txt
python3 scripts/notify_wecom.py --message-file local/wecom-notifications/message.txt --execute
```

每日检查结束时，使用对应报告和观察生成无预约、收齐待整理或缺稿/歧义/检查失败的结果通知：

```sh
python3 scripts/notify_wecom.py --report artifacts/daily-check-DATE-RUN.json --observation local/daily-check/DATE/RUN.json
python3 scripts/notify_wecom.py --report artifacts/daily-check-DATE-RUN.json --observation local/daily-check/DATE/RUN.json --execute
```

通知只包含日期、预约标题、部门及实际结果，不包含稿件正文或平台令牌。使用者已要求正常结果也通知：无预约报告生成“无预约”，收齐允许整理生成“已收齐，整理待完成”；收齐不等于整理完成。整理完成须以任务中的保存重开、正文与图片核对证据为依据，编写已核对的私有消息文件发送。输入文本限制为 1800 UTF-8 字节，这是本脚本的保守上限；超限应人工精简成可核对的摘要，不能直接删掉缺稿后宣称完整。缺配置或发送失败时仍在当前聊天报告，不能把未发送记为已提醒。

发送前将 `dispatch_reserved` 持久写入 `data/wecom-notifications/`，相同群地址及相同消息内容不会因改输入文件名而重发。收到明确 API 零错误码记录 `accepted`，表示平台接受，不证明群成员已读。错误码记录 `rejected`，超时、回复错误或连接异常记录 `outcome_unknown`；进程中断保留发送预留。后三者均不能自动重放，须先核对实际群消息再决定下一步，不能删除回执来绕过去重。回执没有 Webhook 原文或消息正文，凭据更新后群定位变化需要重新核对。

这是单向群提醒，不是聊天指令入口。收到群消息不会启动 agent；定时检查仍由现有定时入口触发。运行电脑无需保持企业微信客户端登录，但需要联网与执行程序可用。迁移时先停旧节点，恢复私有配置和回执，再核对新节点；只有代码不会带走通知配置及去重状态。

离线测试覆盖预检不发消息、不写预留，发消息前的持久预留，相同消息去重，超时不重放且不泄露地址，API 拒绝，配置目的地址约束、异常提醒生成及文本上限。真实群发送状态见下述补充，Windows 执行仍需另行验收。

2026-10-04 补充：使用者在保存前确认机器人创建、私有凭据保存及指定连接测试。手机端显示已添加英文名称的消息推送；Mac 宿主机脚本实际发送一次指定测试文字，回执 `accepted`、API 错误码 0，随后通过 iPhone 镜像在指定群内看到对应机器人及完整测试消息，真实单向提醒验收通过。当前聊天每日 15:10 任务已更新调用该通知脚本。10 项通知离线测试通过，全套 53 项通过；首次每日无人值守和 Windows 实际通知仍待验收。私有目标定位、用户确认及测试状态见 `local/wecom-notifications/state.json`；不在公开文档记录群名、完整地址或成员信息。
