# 问题日志（ISSUE LOG）

> 2026-08-22 全库功能/逻辑审计后的修复记录。范围：src/revenue_agent（Python）与 rust/crates/revenue-gateway（Rust）。
> 验证方式：`py -3 -m pytest tests`（52 通过）、`cargo test --manifest-path rust/Cargo.toml`（6 通过）、独立探针复测。
> 未处理项：#14（.env 明文密钥，按要求保留现状，建议后续轮换）。

---

## 高危

### #1 默认 Python 下单路径无任何风控闸门
- **级别**：高
- **问题**：`CapabilityGate`/`RuntimeCapabilities`（含 `AGENT_MAX_LEVERAGE`、`AGENT_ENABLE_UNCOVERED_SHORT`）从未被下单代码调用，仅在测试中出现。实测：0 持仓直接 `sell` 会变相开空并入账现金；1000 EUR 账户可做空 20000 名义（约 25 倍杠杆）。Rust 网关虽有闸门，但默认 Python 路径完全绕过。
- **位置**：`src/revenue_agent/paper_broker.py`、`src/revenue_agent/cli.py`
- **根因**：治理层只写了"声明"，没有接入执行层；PaperBroker 不感知策略配置。
- **修复方案**：
  - `PaperBroker` 新增 `capabilities` 参数（默认安全档 `RuntimeCapabilities()`），内部持有 `CapabilityGate`；
  - `side == "short"` 必须先过 `require_uncovered_short()`；
  - `"sell"` 数量超过持仓且未开启 uncovered short → 拒绝（与 Rust 的 `uncovered_sell` 对齐）；
  - 下单前做投影检查 `_require_leverage_within_cap`：以成交价估本品种、以持仓均价保守估算其他品种，要求总敞口/权益 ≤ max_leverage；权益非正时仅允许减仓；
  - CLI 所有 PaperBroker 实例统一传入 `RuntimeCapabilities.from_env()`；
  - rebalance 计划改为**先卖后买**排序（避免换仓途中触发杠杆上限）。
- **验证**：`test_paper_broker.py::test_uncovered_sell_is_rejected_by_default`、`test_short_is_disabled_by_default_and_margin_capped_when_enabled`；探针 V1/V1b/V1c 复测通过。

### #2 paper-order 可跳过人工采纳门
- **级别**：高
- **问题**：`cli.py` 中 `if args.strategy_id and ...`——不传 `--strategy-id` 即可绕过"策略必须经人工采纳"的约束，与 README 承诺矛盾。
- **位置**：`src/revenue_agent/cli.py:_paper_order` 及 argparse 定义
- **修复方案**：`--strategy-id` 改为 `required=True`；`_paper_order` 入口处显式拒绝空值；采纳校验改为无条件执行；README 示例同步更新。
- **验证**：`test_cli.py::test_paper_order_requires_an_adopted_strategy_id`（缺参 SystemExit、未采纳 PermissionError）。

### #3 Insight ID 冲突可静默篡改已采纳洞察
- **级别**：高
- **问题**：ID 由 LLM 输出直接采用（可为任意/空字符串），`save_insight` 用 `INSERT OR REPLACE`。同 ID 二次生成会覆盖 title/thesis/direction，而 `strategy_adoptions` 仍指向该 ID——人工审核被架空。空 ID 时多条洞察互相覆盖。
- **位置**：`src/revenue_agent/insights.py:_validate`、`src/revenue_agent/storage.py:save_insight`
- **修复方案**：ID 改为服务端确定性内容哈希（sha256(title/thesis/entities/horizon/direction/invalid_if/evidence_ids) 前 24 位），完全不信任 LLM 提供的 id；内容相同 → 同 ID 幂等覆盖无害；内容不同 → 不同 ID，不可能碰撞替换。
- **验证**：`test_insights.py::test_insight_id_is_derived_from_content_not_llm_choice`；探针 V4。

### #4 ToolPlanner 原生 tool_calls 路径零校验
- **级别**：高
- **问题**：allowlist 与"最多 8 个调用"限制只在 JSON 文本回退分支生效；`complete_response` 原生工具调用分支直接透传（实测 12 个未注册工具名全部通过 plan 阶段），违反 README 的规划器承诺。
- **位置**：`src/revenue_agent/tooling.py:ToolPlanner.plan`
- **修复方案**：抽取共享的 `_validated_calls()`：两条路径统一做"≤8 个 + 名称必须在 allowlist + arguments 必须为 dict"校验，违规即抛 `ValueError`；顺带修正 JSON 路径缺失 `calls` 字段时的误导性报错。
- **验证**：`test_tooling.py::test_native_tool_calls_are_allowlisted_and_capped`；探针 V3。

## 中危

### #5 部分平仓重置剩余持仓的平均成本价
- **级别**：中
- **问题**：Python 条件 `old*signed <= 0` 与 Rust 条件 `signum() != signum()` 把"减仓"误判为"开仓/换仓"，卖出后 avg 被重置为成交价（买 10@100 卖 5@110 → 剩余股 avg=110）。未来据此计算已实现/未实现盈亏必错。
- **位置**：`src/revenue_agent/paper_broker.py`（原 :40）、`rust/crates/revenue-gateway/src/lib.rs:submit_order`
- **修复方案**：三态判定 `_merged_average()` / Rust 等价逻辑：开仓或穿零翻转 → 成交价；部分减仓 → 保留原均价；同向加仓 → 加权平均。两侧行为一致。
- **验证**：`test_paper_broker.py::test_partial_sell_keeps_original_average_price`、`test_flip_through_zero_reprices_at_trade`；Rust `partial_reduction_keeps_original_cost_basis`；探针 V2。

### #6 缺价 mark 炸掉整条流水线 + 调度线程静默死亡
- **级别**：中
- **问题**：`mark_to_market` 要求所有持仓有价，否则抛异常；`_cycle` 在持有未报价品种时整个 cycle 崩溃；更严重的是 `scheduler._run` 无异常保护，回调抛错会让 paper-month 定时任务线程静默死亡，主进程仍返回退出码 0。
- **位置**：`src/revenue_agent/cli.py:_cycle/_paper_month`、`src/revenue_agent/scheduler.py`
- **修复方案**：
  - `_cycle` 的自动 mark 包裹 try/except ValueError → 打印 `mark_skipped` 继续；
  - `_paper_month` 学习回调对"无新数据"ValueError 打印 `learning_skipped`；
  - `AgentScheduler._safe_callback()`：捕获 `Exception`、打印完整 traceback、循环继续（BaseException 仍向上传播）；单次失败不再杀死线程。
- **验证**：`test_replay_scheduler_rebalance.py::test_scheduler_survives_callback_exceptions`；探针 V5（一次异常后继续第 3 次运行）。

### #7 学习器不幂等：每次都用全历史收益
- **级别**：中
- **问题**：每次 `learn` 用首日→最新全历史算收益率，同一份亏损历史反复调用会每次再压 5% 直到下限；`observations`/`last_return` 存了不用。
- **位置**：`src/revenue_agent/learning.py`、`src/revenue_agent/storage.py`（schema）
- **修复方案**：
  - `learning_state` 增加 `last_mark_at` 列（`ALTER TABLE` 原地迁移，兼容旧库）；
  - 每次学习只消费自上次学习点之后的新 mark 窗口，并把上一个已消费 mark 作为收益基准锚点（一条新 mark 即可产生增量窗口）；
  - 无新数据时抛出明确 ValueError（幂等），重复调用不再叠加偏移。
- **验证**：`test_learning.py::test_repeated_learn_on_unchanged_history_is_a_noop`（第二次调用抛错；新 mark 后窗口收益精确等于两 mark 比值）。

### #8 Rust 网关：max_leverage 从未启用；snapshot 把无报价持仓按 0 计价
- **级别**：中
- **问题**：`RiskConfig.max_leverage` 定义后从未使用（开启 uncovered short 后可无限做空）；`snapshot()` 对缺 quote 的持仓按 0 计价且不查报价新鲜度，equity 失真会传导到 rebalance 权重计算。
- **位置**：`rust/crates/revenue-gateway/src/lib.rs`
- **修复方案**：
  - 新增 `quote_is_fresh()`/`reference_price()`/`projected_equity()`/`projected_gross_exposure()`/`current_gross_exposure()`；
  - 风险增加型成交（投影敞口变大）要求：投影权益为正 且 总敞口 ≤ max_leverage × 投影权益，否则 `equity_exhausted`/`leverage_exceeded`；纯减仓单始终放行；
  - `snapshot(now_epoch)`：过期/缺失报价的持仓回退按成本基差计价，并通过新增字段 `unpriced_symbols` 显式上报；
  - 投影权益计入 cash_delta（空头保证金收入/手续费），避免误判。
- **验证**：Rust `leverage_cap_bounds_uncovered_shorts_even_when_enabled`、`snapshot_falls_back_to_cost_for_stale_quotes_and_reports_it`。

### #9 Moltbook 无效时间戳被伪造为 now()
- **级别**：中
- **问题**：`_parse_time` 对缺失/非法时间一律回退当前时间，事件时间变成抓取时间，破坏 replay"只用事件时点前的数据"的对齐假设与前瞻防护。
- **位置**：`src/revenue_agent/moltbook_source.py`
- **修复方案**：`_parse_time` 返回 `None` 表示不可解析；`fetch()` 直接丢弃无法确定时间的帖子（宁可少一条证据，不伪造时间线）。
- **验证**：`test_insights.py::test_moltbook_posts_without_valid_timestamp_are_dropped`。

## 低危

### #10 Scheduler 回调慢于 interval 时背靠背连跑
- **级别**：低
- **问题**：`next_run += interval` 在回调超时后会落后于当前时间，等待量归零导致连续补跑（catch-up debt）。
- **位置**：`src/revenue_agent/scheduler.py:_run`
- **修复方案**：节拍从"完成时刻"起算——每轮结束后 `Event.wait(interval)`，回调耗时不可能积累债务；间隔语义变为"上一轮结束到下一轮开始"。
- **验证**：探针 V5 间隔稳定 0.51s（含一次异常）。

### #11 insights "最多 3 条"未强制
- **级别**：低
- **问题**：系统提示词要求最多 3 条，但解析时只检查是 list，LLM 返回任意条数都会被接受入库。
- **位置**：`src/revenue_agent/insights.py:generate`
- **修复方案**：新增常量 `MAX_INSIGHTS_PER_RESPONSE = 3`，超出即抛 `ValueError`（与 planner 的 8-call 校验同等严格风格）。
- **验证**：`test_insights.py::test_more_than_three_insights_are_rejected`。

### #12 网关 state 文件并发写入互相覆盖
- **级别**：低
- **问题**：每次 CLI 都起新网关进程读写同一个 state JSON，两个进程并发时 last-writer-wins，可能丢失成交记录。
- **位置**：`rust/crates/revenue-gateway/src/main.rs`
- **修复方案**：进程生命周期内持有排他锁文件（`create_new` + PID 写入）；锁文件随每次 save 刷新 mtime，超过 30 秒视为崩溃残留可接管；`Drop` 时删除；获取失败（等待 ~12s 后）以退出码 2 报错退出。
- **验证**：`cargo test` 协议集成测试通过（含锁获取/释放路径）。

### #13 审计记录在成交之后追加，中途失败订单无审计
- **级别**：低
- **问题**：订单先执行、审计后落盘；若 append 失败或进程中断，成交将无审计痕迹。
- **位置**：`src/revenue_agent/cli.py:_paper_order/_rebalance`
- **修复方案**：改为两阶段审计——执行前追加 `paper_order_intent` / `barbell_rebalance_intent`（含完整意图），执行后追加 `paper_order_result` / `barbell_rebalance_result`（含结果）。append-only 账本保证任何中断点都可事后对账。
- **验证**：`test_cli.py::test_adopted_strategy_can_place_paper_order_and_leaves_audit_trail`。

## 修复过程中发现的关联缺陷（一并修复）

### R1 rebalance 买入腿未预留手续费，卖后买必然资金不足

- **级别**：中（修复 #1/#8 时由回归测试暴露）
- **问题**：计划按 `权重差 × equity` 定买入名义额并试图把残余现金全部部署进目标权重，但没预留双边手续费。实测卖 AMD 24,024.5 后现金 25,902.5，买入 TLT 需 25,926.5+25.9 手续费 → `insufficient paper cash`，CLI rebalance 路径必现。
- **位置**：`src/revenue_agent/rebalancer.py`
- **修复方案**：
  - `_reserve_fee_budget()`：用 `BarbellConfig.transaction_cost_bps` 估计整轮费用，从买入腿等比预留；
  - `execute_barbell_orders()`：单腿被风控/市场拒绝时打警告跳过并继续其余腿，不再整体崩溃。
- **验证**：`test_rebalance_sells_execute_before_buys`（满仓换仓场景端到端通过）；`test_barbell_orders_are_weight_deltas` 断言更新为"总额 ≤ 目标且含费用预留"。

### R2 OpenCode API 大负载 + json_object 组合必现 HTTP 500（真实 cycle 测试发现）
- **级别**：中
- **问题**：`cycle --moltbook` 真实调用 opencode.ai `/zen/go/v1/chat/completions` 时：60s 读超时（默认超时对长生成不够）→ 调到 240s 后稳定返回 **HTTP 500**。二分定位：完整负载 + `max_tokens=6000` + `response_format=json_object` 三者组合才触发；去掉任一因素均正常（同负载无 json_mode 时 18 秒返回合规 JSON）。属网关服务端缺陷。
- **位置**：`src/revenue_agent/stepfun_client.py:complete_response`、`src/revenue_agent/insights.py:generate`
- **修复方案**：
  - 客户端新增 `json_mode: bool = True` 开关（默认行为不变）；洞察路径传 `json_mode=False`——系统提示词本就强制 JSON-only，`_parse_json` 可剥代码围栏；
  - 提示词构造抽成 `_messages()` 复用；用 `inspect.signature` 特性探测保持对自定义 LLM 桩的兼容。
- **验证**：真实 cycle 全链路通过：20 条 Moltbook 事件 → deepseek-v4-flash 返回 3 条洞察 → 入库 → 采纳 → 下单 → mark。52 个 Python 测试保持全绿。
- **备注**：默认 60s 超时对该端点偏短，长生成建议 `OPENCODE_GO_TIMEOUT_SECONDS=240`；客户端暂无 5xx/超时自动重试（见遗留事项）。

### R3 REPL 管道输入首行带 BOM 导致命令失配（真实测试发现）
- **级别**：低
- **问题**：Windows PowerShell 管道送入 REPL 的首行携带 UTF-8 BOM（`\ufeff`），`str.strip()` 不去除 U+FEFF，首行命令永远落入自然语言兜底分支（此前 `status` 恰好被关键词模糊匹配掩盖）。交互式键入不受影响，仅影响脚本化管道调用。
- **位置**：`src/revenue_agent/cli.py:_repl`
- **修复方案**：读取后 `lstrip("\ufeff")` 再解析。
- **验证**：管道 `"insights`nquit"` 此前打印"我还不理解"，修复后正确列出洞察。
- **相关说明（非缺陷）**：Windows PowerShell 5.1 管道向原生命令传参默认按 ASCII 编码，中文命令会变成 `???`——这是测试管道的固有限制；交互式输入走控制台 API 不受影响，中文自然语言命令在交互 REPL 下工作正常。

---

## 行为变更说明（对使用者可见）

1. `paper-order --strategy-id` 现在为必填参数（原可选）。
2. Python paper 账户默认禁止裸卖空与超限 sell；需要做空必须显式设置 `AGENT_ENABLE_UNCOVERED_SHORT=true`，且受 `AGENT_MAX_LEVERAGE` 约束（与 Rust 网关一致）。
3. `learn` 幂等：没有新 equity mark 时提示"没有新的 paper mark 可以学习"而不是重复调整权重。
4. insight ID 由系统生成（24 位内容哈希），人工采纳后不会再被同 ID 内容静默改写。
5. Rust snapshot 响应新增 `unpriced_symbols` 字段（向后兼容，仅新增键）。
6. 审计账本出现新的记录类型：`paper_order_intent`/`paper_order_result`/`barbell_rebalance_intent`/`barbell_rebalance_result`（替代原 `paper_order`/`barbell_rebalance` 单段记录）。

### R4 彻底移除 StepFun API + 打包依赖补全 + 可编辑安装（2026-08-22）
- **级别**：重构/加固（应用户要求）
- **问题**：
  1. 代码中残留 StepFun 集成（`stepfun_client.py`、`config.stepfun_config()`、`.env` 的 `STEPFUN_*` 键、README 说明、导出符号），虽然运行时只配置了 OpenCode 端点，但类名与配置仍指向 StepFun；
  2. `pyproject.toml` 声明 `dependencies = []`，而代码硬依赖 `requests` 与 `yfinance`，新机器 `pip install -e .` 后无法运行；
  3. 项目未安装，必须手动设置 `$env:PYTHONPATH="src"` 才能启动。
- **修复方案**：
  - `stepfun_client.py` 重命名为 `llm_client.py`：`StepFunClient → LLMClient`、`StepFunQuotaError → ProviderQuotaError`，错误文案与文档全部去除 StepFun 字样；该客户端为通用 OpenAI 兼容实现，唯一配置来源是 `opencode_go_config()`（OpenCode 网关）；
  - 删除 `config.stepfun_config()`；`__init__.py` 导出改为 `LLMClient`/`ProviderQuotaError`；
  - `.env` 与 `.env.example` 移除全部 `STEPFUN_*` 条目（原 STEPFUN_API_KEY 已暴露于明文文件，**建议在 StepFun 控制台吊销该密钥**）；
  - README Agent Runtime 一节改述为 OpenCode planner 并注明 StepFun 已移除；
  - 测试迁移：`tests/test_stepfun_client.py → tests/test_llm_client.py`，`test_tooling.py` 导入路径同步更新；
  - `dependencies = ["requests>=2.31", "yfinance>=0.2"]`；
  - 执行 `pip install -e .`，此后任意目录直接使用 `revenue-agent` 命令，无需 PYTHONPATH。
- **验证**：`grep -i stepfun` 全库仅剩历史记录与本条目；52 个测试通过；全新 shell 中 `revenue-agent status` 免 PYTHONPATH 正常执行。

### R5 OpenCode 默认超时过短 + 无瞬断重试（真实 REPL 使用触发）
- **级别**：中
- **问题**：REPL「运行研究」实测报 `Read timed out (read timeout=60.0)`——`OPENCODE_GO_TIMEOUT_SECONDS` 默认 60s 对该共享网关的长生成明显不够（此前真实 cycle 已证明需要 ~20–240s），且客户端对超时/5xx 无重试，单次网络抖动即失败。
- **位置**：`src/revenue_agent/config.py:opencode_go_config`、`src/revenue_agent/llm_client.py`
- **修复方案**：
  - 默认超时 60s → **240s**（环境变量 `OPENCODE_GO_TIMEOUT_SECONDS` 仍可覆盖）；
  - `LLMClient` 新增 `_post_chat()` 重试机制：读超时、连接错误、HTTP 5xx 按 2s/4s 指数退避自动重试（默认最多 2 次重试）；4xx 与配额错误（402）不重试立即抛出；凭证不入日志；
  - 构造函数新增 `max_retries: int = 2`。
- **验证**：新增 3 个回归测试（503 后成功重试、超时后成功重试、耗尽后抛最后错误 + 4xx 不重试）；55 个测试全绿；进程内驱动 REPL「运行研究」端到端成功（20 事件 → 3 洞察 → mark）。

## 遗留事项

- **#14（部分完成）**：`.env` 明文密钥问题——STEPFUN 密钥已随 R4 从 `.env` 移除（**请到 StepFun 控制台吊销该 key**）；OPENCODE_GO 与 MOLTBOOK 密钥仍明文存放于 `.env`，建议后续改用秘密存储并轮换。`.gitignore` 已忽略 `.env` 且项目尚未 git 化。
- `learning_state.last_mark_at` 通过 `ALTER TABLE` 自动迁移，首次用旧数据库运行时会自动补列，无需手工操作。
- ~~LLM 客户端无 5xx/超时自动重试~~ 已由 R5 修复（指数退避重试 + 默认超时 240s）。
- `pyproject.toml` 的 `dependencies = []` 未声明 `requests`/`yfinance`，新机器 `pip install -e .` 后需手动补装。

## 真实链路验证记录（2026-08-22）

`py -3 -m revenue_agent --db data/opencode_cycle_test.db cycle --symbols AMD NVDA --moltbook`

- Moltbook 抓取 20 条帖子 → OpenCode API（deepseek-v4-flash）生成 3 条洞察（max-3 校验生效）→ 入库；
- 洞察 ID 为 24 位内容哈希（#3 修复生效），REPL `/insights` 正常列出；
- 采纳 `1634a0874cd2c0c5dadecd3b` → `paper-order` 成交 → `mark` 后 equity 1009.8；
- 审计账本含 `research_cycle`、`strategy_adoption`、`paper_order_intent/result` 完整链。
