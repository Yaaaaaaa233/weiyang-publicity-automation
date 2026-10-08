# 每轮日志、用量与保留策略

2026-10-08 第一阶段实现。日志位于 `local/run-logs/RUN_ID/`，不提交 Git。`daily_run` 和 `full_run` 自动记录状态、阶段耗时、浏览器动作结果及证据索引；正文和截图留在原私有证据位置，日志不重复复制证据。

| 文件 | 内容 |
| --- | --- |
| `summary.json` | schema_version、运行标识、业务日期、节点、实际起止时间、各阶段状态、预约和整理数量、转存和通知状态、人工介入、用量状态、日志字节数及证据索引 |
| `events.jsonl` | 追加记录步骤、时间、结果、耗时和错误类别；不写提示词、稿件正文或异常全文 |
| `usage.jsonl` | 真实单次调用的用量；同请求去重，冲突拒绝 |

## 用量与人工介入

用量初始为 `unknown`，不是零消耗。执行端从模型实际调用记录导出以下格式，存入私有 JSON 数组，再导入：

```json
[{
  "request_id": "真实请求标识",
  "model": "真实模型标识",
  "source": "provider",
  "started_at": "2026-10-08T15:00:00+08:00",
  "finished_at": "2026-10-08T15:00:10+08:00",
  "input_tokens": 100,
  "cache_read_tokens": 1000,
  "output_tokens": 20,
  "cache_write_tokens": 0
}]
```

上面数字仅示范结构。`input_tokens` 定义为非缓存输入；若来源把缓存包含在总输入内，导出端需正确拆分，不能重复计算。无法取得的字段用 null。时间必须带时区并落在本轮开始之后，来源累计数字不能填作单次调用。DSH 当前尚未完成真实单轮用量导出适配，第二阶段须核对其实际接口和记录格式；本轮只提供严格导入契约。

```sh
python3 scripts/run_log.py usage --run RUN_ID --date YYYY-MM-DD --file local/usage-export.json
python3 scripts/run_log.py intervention --run RUN_ID --date YYYY-MM-DD --kind approval
python3 scripts/run_report.py --run RUN_1 --run RUN_2 --output artifacts/trial-report.json
```

人工介入还可记录 login、unlock、manual_repair，由实际观察的 agent/操作者记录。该计数不能自动证明没有审批或登录介入。部署、调试与日常运行使用不同运行标识，三天评估只选择日常运行。

汇总默认费用未知。可用 `--prices local/prices.json` 提供按模型映射的价格，必须注明 source、as_of、currency，并分别提供 `input_tokens_per_million`、`cache_read_tokens_per_million`、`output_tokens_per_million`、`cache_write_tokens_per_million`。缺用量或价格即标未知，不同币种不相加；结果是有依据的估算，不是账单回执。

## 清理与容量

```sh
python3 scripts/run_log.py cleanup
python3 scripts/run_log.py cleanup --execute
```

默认预览。仅处理日志自身的 events/usage 文件：结束且未受保护的记录满 7 天压缩，满 90 天删除明细，摘要保留。活跃、待确认及未解决运行受保护，不自动清理。请求汇总在摘要中保留；压缩后仍可生成成本报告。

每次新增浏览器快照或截图前，检查 `local/` 和 `artifacts/` 总体积，默认上限 5 GiB。达到上限停止新增证据并报告，不能一边丢证据一边宣称完成。单个快照可能让体积略超上限，下一次采集会停止；此上限不包含 Docker 浏览器卷。

**当前清理不会删除原稿基线、任务证据、任务账本、通知回执和副本映射。** 它们涉及去重与接续；日志清理不是整个项目的清空。正常证据 14 天、异常证据 90 天的自动淘汰仍需结合任务引用与退休归档设计，尚未开启。三天测试记录实际增长速度，之后再确定原始证据归档政策；当前以总量停止保护控制磁盘增长。
