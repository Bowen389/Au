# 自动金额模拟盘：部署步骤

⚠️ 纯模拟，不接交易所，也不会使用你的真实资金。已回填 2026-02-01 起的 10,000 USDT，截止 2026-09-27 完整日 K，模拟账户 9,804.82 USDT。明细见 `paper_fills.csv`，风险与测试方法见 `README.md`。

1. 将整个项目上传自己的 GitHub Private 仓库（要包含 `.github/workflows/paxg.yml`、`paper_portfolio.json` 和 `paper_fills.csv`）。
2. 仓库 Settings → General 启用 Issues；Settings → Actions → General → Workflow permissions 选 Read and write permissions。
3. Actions → PAXG paper account daily signal → Run workflow，选 `mode=signal` 测试。**不要选 init**：模拟盘已经用 10,000 USDT 初始化。每天 UTC 00:20（北京时间 08:20、日本 09:20）系统尝试读取完整日线和即时公开报价，按模拟余额计算交易额、自动写入模拟账本，再发 GitHub Issue。
4. 检查 `paper_portfolio.json` 和 `paper_fills.csv`、仓库 Issues、Actions 日志。测试邮件必须去 GitHub Settings → Notifications 设置并实际核实；GitHub 不保证自己的机器人 Issue 会发邮件。失败时以 Actions 日志为准，**不得拿旧信号去真实交易**。
5. 若要换本金或起始日期，在一个新的、没有 `paper_portfolio.json` 的项目里运行 `python paper.py --init 本金 --start YYYY-MM-DD` 后再部署，不能直接改旧账本本金。任何模拟成交都不是交易所真实成交。
