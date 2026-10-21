# Revenue Agent 全仓审计（2026-08-27）

本目录只存放审计产出，**不修改任何业务代码**。

| 文件 | 内容 |
|---|---|
| [2026-08-27-full-audit.md](2026-08-27-full-audit.md) | 全仓审计报告：架构判断、上一轮 ISSUE_LOG 验收、缺陷、双路径漂移、文档冲突、测试缺口 |
| [2026-08-27-recommendations.md](2026-08-27-recommendations.md) | 修复与改进建议：优先级、具体改法、不要做什么 |

范围：`src/revenue_agent/`、`rust/crates/revenue-gateway/`、`tests/`、`README.md`、`ISSUE_LOG.md`、`HEARTBEAT.md`、`pyproject.toml`。

方法：源码通读 + 跨模块对照 + 现场探针（NaN/Inf 杠杆、非正权益减仓、洞察 confidence 覆盖、短代码子串匹配）+ 对照可审计交易/LLM 闸门的业界做法。

本轮**没有改代码、没有跑完整 pytest/cargo 作为准入门槛**；探针用独立 `py -3 -c` 复现了若干高优先级行为。
