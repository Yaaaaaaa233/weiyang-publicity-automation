# Windows 人工会话受限测试

本测试使用 Windows 本机项目 D:\Study\weiyang-publicity-automation。不要使用 Mac 路径，不要求会话 header.cwd 已是项目目录；执行工具时显式指定项目工作目录，并在回执分别记录会话目录与实际项目目录。插件自动投递的桌面验收仍要求绑定会话 header.cwd 与项目目录一致，不能用本人工测试替代。

部署后先核对 AGENTS.md 和 docs/manual-trigger-desktop.md 均存在，按文件完整读取。业务关闭。只允许写一份新的 local/wecom-control-test/manual-results/manual-test-实际时间.json，不另写脚本、输出日志或修改旧回执。JSON 包含 observed_at（真实上海时区时间）、project_root、session_cwd（如实际可知）、rules_read、status=manual_test_completed、business_executed=false、trigger_source=manual_human_prompt、plugin_dispatch_verified=false、wecom_inbound_verified=false。写后回读校验。缺文件或其他失败写真实阻断，不能补造成功。

不调用浏览器、不读取凭据、不整理稿件、不转存、不通知、不安装插件、不改调度或原账本。当前 Windows 没有安装接续插件的验收记录，Mac 的 bridge_verified 不等于 Windows 通过。此步骤仅消除环境/部署差异并确认受限任务执行。
