# Revenue Agent 审计改进建议

- **日期**：2026-08-27
- **配套**：同目录 `2026-08-27-full-audit.md`（缺陷与证据）
- **约束**：本文件是建议，不是补丁。落地时应另开改动，并补回归测试。

编号 `H*` / 现象对应审计报告。优先级按「错误叙事 + 错误数字」→「双账本」→「研究诚信」→「文档与测试」排列。

---

## 原则

1. **先统一语义，再加功能。** 在账本、费用、杠杆、采纳四件事对齐之前，不要接 IBKR、不要让 planner 注册下单工具、不要把 `apply_adopted` 接到 `PaperBroker`。
2. **LLM 只出意图。** 价格、风险、仓位、成交必须是确定性代码。这条今天大体成立，后续接线时不要破坏。
3. **失败要关闭。** `nan`/`inf`、坏 JSON、锁争用、缺价加风险，一律拒绝并留下审计，不要静默成「新账户」或「成功 0 笔」。
4. **文档必须能当合同。** README 能力表、paper-month 语义、Language Boundary 与代码不一致时，先改文档或先改代码，不要两者都留。

---

## P0 — 立刻（正确性 / 安全叙事）

不改架构也能做。每一项都应带一个会失败的回归测试。

### P0.1 拒绝非有限数字

- 所有 `notional` / `price` / mark / `AGENT_MAX_LEVERAGE`：`math.isfinite(x) and x > 0`（杠杆帽另外要求 `>= 1`）。
- 从 SQLite 读出的 cash/qty 若非有限，拒绝下一笔成交。
- Rust：`request.notional.is_finite()`；加载后 `cash` 非有限则失败关闭。
- 覆盖：`nan`、`inf`、`-inf`、空字符串（已 fail-close 的保持）。

对应：H4，Inf 卖单把现金打成 NaN。

### P0.2 非正权益只允许减风险单

与 ISSUE_LOG #1 文案、Rust `projected_gross` 上升才检查、报错字符串对齐：

- 计算旧/新 gross。
- `new_gross <= old_gross + eps` → 放行（含 cover/sell 减仓）。
- 权益已非正 → 只允许严格降低 `|exposure|` 的单。
- 收紧 `max_leverage` 之后必须还能卖掉回到帽内。

对应：H3。补测试：亏钱空头小额 cover；权益为负时全平。

### P0.3 洞察身份含 confidence；已采纳行禁止静默 REPLACE

- `_content_id` 纳入 `confidence`（以及稳定排序后的 `affected_entities`）。
- `save_insight`：若 `strategy_adoptions` 已有该 id，内容变化则拒绝或写新 id，不得 `INSERT OR REPLACE` 覆盖已审核字段。
- CLI `_seed` 式测试改为走 `_validate`，不要手写攻击者 id。

对应：H5。

### P0.4 符号规范化

- 经纪、报价、CLI `--symbol` / `--attack-symbol` / `--defense-symbol` / `--symbols` 在边界 `strip().upper()`。
- 空、纯空白、非法字符拒绝。
- 再平衡缺价不再 `continue`，而是失败（计划阶段）。

对应：H7。

### P0.5 轮换密钥

- 轮换磁盘 `.env` 里的 `OPENCODE_GO_API_KEY` 与 `MOLTBOOK_API_KEY`。
- 若 R4 留下的 StepFun key 仍在供应商侧有效，在控制台吊销。
- 不把密钥写进审计文档、测试或 JSONL。

对应：#14。

---

## P1 — 下一刀（架构：单一账本 + 真闭环）

### P1.1 选定唯一 paper 账户

二选一，不要第三条「以后再对账」：

**方案 A（推荐短期）**：Python `PaperBroker` + SQLite 为唯一账本。`--rust-broker` 标成实验开关，CLI 启动时打印「另一账户、另一现金、无审计、不进学习」，或直接从 CLI 拿掉直到 P1.2 完成。

**方案 B**：Rust gateway 为唯一执行器。必须同时做到：

- `RiskConfig` 从环境/文件加载，默认与 CLI `--initial-cash`、`AGENT_MAX_LEVERAGE`、`AGENT_ENABLE_UNCOVERED_SHORT` 一致。
- 每笔 fill 写同一条 JSONL 审计（intent/result）。
- 成交回写 SQLite `paper_*` 或学习改为读网关 snapshot。
- `status` 显示这一个账户。
- 真正的 OS 文件锁（Windows `LockFileEx` / Unix `flock`），持有 fd 直到进程结束；坏 JSON **exit ≠ 0**，禁止变全新 100k 账。
- 幂等键由 CLI 按 `(strategy_id, symbol, side, notional, client_intent_id)` 稳定生成。
- **禁止** `submit_market_order` 里用用户 `--price` 覆盖 quote。Paper 若允许指定价，协议里就叫 `client_price`，不要叫 server quote。

对应：H1、H8、H9、H10。

### P1.2 采纳约束策略，不只是 id

下单与再平衡在 `has_adoption` 之后：

- insight 必须仍存在。
- `symbol` ∈ `affected_entities`（规范化后）。
- `side` 与 `direction` 相容（`long` 不能 `short`/`sell` 开空；`neutral` 默认拒绝交易）。
- 可选：`invalid_if` 人工确认尚未触发。
- `PaperBroker` 库路径若继续对外，要么同样强制 `strategy_id`，要么文档明确「仅 CLI 有治理」。

对应：H5。

### P1.3 `learn` 必须驱动配置，否则改文档

若保留「学习」产品语义：

- `_rebalance` / `plan_barbell_orders` 读取 `learning_state.attack_weight` / `defense_weight` 填入 `BarbellConfig`。
- 审计记录本次权重来源（学习窗口、收益率、回撤、原因）。
- `paper-month` 要么在 cycle 后暂停等人 `adopt`+`rebalance`，要么改 README 为「研究采集器，不仿真交易」。

对应：H6。

### P1.4 再平衡失败必须可见

- `execute_barbell_orders`：任一计划腿失败 → 非零退出，result 记录 `skipped` + 原因。
- 禁止「0 fills + exit 0」。
- 默认不要把非攻防持仓削成 0；若要 flatten_others，做成显式 CLI 开关。
- 费用预留使用**成交** `fee_bps`，不要 15 vs 10 的巧合。
- 测试断言成交后现金/数量/权重，而不是 `total <= 100_000`。
- 生产 `max_turnover` 与测试不要差 4 倍还不说明。

对应：H8、R1 测试空洞、无关标的被平。

### P1.5 Rust 锁与持久化

即使暂缓 Rust 当唯一账本，只要二进制还在：

- 持有锁 fd；Drop 仅当 `pid == self`。
- 空闲也要心跳刷新，或改用内核锁、不再用 30s mtime。
- `load_gateway` 文件存在但不可读/不可解析 → 失败关闭。
- `save` 失败 → JSON 响应 `persist_failed`，不要返回 fill。
- tmp 文件 `sync_all`，再替换；Windows 上处理目标正在被读的 `ACCESS_DENIED`。
- 状态 schema 加版本；缺字段不要静默变新账户。

对应：H10。

---

## P2 — 研究诚信（回放可辩护）

在「启发式、不可当回测」写进 README 之前或同时做这些。

### P2.1 日线 as-of

- `_prior_return` / Replay 使用**严格上一交易日（或上一根已收盘 bar）**，不要 `observed_at <= event` 把当日收盘算进去。
- 测试必须种「同日历日、时点早于收盘」的日线，证明不会漏进当日 close。

### P2.2 基本面历史与时点

- `fundamentals` 主键改为 `(symbol, observed_at)`。
- 时间戳用发布/抓取的可追溯时点，并在 README 写明 yfinance 非 point-in-time、存在重述与存活者偏差。
- `ResearchPipeline.run` 与 `ReplayRunner` 使用同一 as-of 过滤。

### P2.3 实体匹配

- 符号匹配用词边界或符号表，禁止 `"on" in "continues"`。
- 词袋至少要求独立 token。
- `affected_entities` 必须是 list；字符串拒绝，不要拆成字符。
- 空 title/thesis 拒绝。

### P2.4 分数卫生

- 丢弃 NaN OHLCV / 基本面。
- `_prior_return` 对窗口内 `close<=0` 返回 0 或跳过，不要除零打爆 cycle。
- 常数价格相关不要当成合格对冲对（观测不足或方差为 0 → 拒绝）。

### P2.5 LLM 边界收紧

- 洞察路径**保证** `json_mode=False`（不要靠 signature 探测），并测试出站请求没有 `response_format=json_object`。
- `LLMClient` 构造默认 timeout 与 `opencode_go_config` 一致（240）。
- 包装器 `complete(messages, **kwargs)` 不得悄悄回到 json_mode。
- prompt 不要再向模型要 `id`。
- `apply_adopted` 若启用：用洞察的 entities/direction，而不是对全部事件跑词袋。

### P2.6 Moltbook / Scrapling

- focus 排序在截断之前，或真正翻页（`has_more` / cursor）。
- 拒绝未来 `created_at`。
- `posts is None` 当空列表。
- Scrapling：实现发布时点或标明「只能用于实时证据、禁止 replay」；检查最终 URL 主机。
- HEARTBEAT：实现 30 分钟检查，或删除 `HEARTBEAT.md` 与过期的 `memory/heartbeat-state.json`。半份运维协议比没有更危险。

---

## P3 — 治理、审计、运维卫生

### P3.1 审计账本

- 追加时进程锁 + `flush` + `fsync`。
- `allow_nan=False`。
- 截断/空行：`verify()` 返回 false，构造时不要炸。
- `--rust-broker` 与 Python 写同一 JSONL。
- `adopt` / `research_cycle` 改为 intent/result，或接受「研究写入无两阶段」并改 ISSUE_LOG。
- 无 HMAC/外锚时，文档写明：防误改，不防持有写权限的人重写或尾截断。

### P3.2 SQLite

- `PRAGMA busy_timeout`；`foreign_keys=ON`。
- `strategy_adoptions.insight_id` → `insights(id)`。
- 学习 RMW 放进同一事务。
- `CREATE TABLE` 直接包含 `last_mark_at`；ALTER 用 `PRAGMA table_info` 判断，不要吞掉所有 OperationalError。
- `.gitignore` 增加 `*.db-wal`、`*.db-shm`、`memory/`、`*.egg-info/`。

### P3.3 调度与退出码

- 长期任务：回调失败计数；超过阈值非零退出或停跑。
- `_safe_callback` 用于 `run_once`。
- KeyboardInterrupt 不要伪装成成功（exit 0）除非文档如此承诺。
- `cover`：必须已有空头，数量 ≤ `|short|`。
- `short` 作为减多：文档化，或要求用 `sell`。

### P3.4 REPL

- `/order` 参数数量不对 → 报错，不要掉进自然语言。
- 自然语言改为整词/整句，不要子串。
- 补 `/adopt` 或从帮助/README 去掉 REPL 下单示例。
- REPL 的 moltbook 与 CLI 一样需要显式开关，不要「有 key 就抓」。

### P3.5 能力表诚实化

README 能力政策改为「实际生效」：

- 生效：`AGENT_ENABLE_UNCOVERED_SHORT`、`AGENT_MAX_LEVERAGE`（修完 nan 后）、CLI 强制采纳。
- 未实现：live broker / copy trading / external tokens（Protocol 存在，无实现，设 true 无效果）。
- `AGENT_REQUIRE_HUMAN_ADOPTION`：要么接到 CLI，要么从文档删除。

`LLMClient` 默认 timeout、Language Boundary、默认 db 路径与示例 notional 一并改到与代码一致。

---

## P4 — 测试还债（与 P0–P3 绑定，不要单独堆测试）

最低集，缺一则对应修复不算完成：

1. `AGENT_MAX_LEVERAGE` ∈ {nan, inf, 0, ""} 的 from_env / gate 行为。
2. 非正权益减仓放行；超帽减仓放行。
3. 已采纳 insight + 仅改 confidence → 不得覆盖或必须新 id。
4. `amd` vs `AMD` 下单/mark/rebalance。
5. 未授权 Rust submit 的响应 `ok:false` 且 `code=strategy_not_adopted`。
6. `--rust-broker` 与 `--db` 隔离（或统一后的同一账户断言）。
7. 再平衡：跳过卖则不得买；exit code；费用预留使 `buy_total + fees <= cash_after_sells`。
8. `learn` 权重进入 `BarbellConfig`（若做 P1.3）。
9. `paper-month --once` 在已采纳 insight 下仍零成交（若保持不下单语义）或按新语义成交。
10. 同日日线不进入 `_prior_return`。
11. 短代码 `ON` / `AI` 不因子串匹配。
12. 锁争用或坏 JSON 加载（Rust）。
13. REPL 5 段 `order` 不触发 cycle；BOM 首行。
14. 出站 insight 请求无 `json_object`。
15. 密钥不出现在 `/config`、ledger、异常文本。

删掉或改名过满的测试：`stdin_protocol_enforces_authorization_before_fill`（现在不 enforce）、`test_barbell_orders_are_weight_deltas` 的费用预留声明。

---

## 明确不要做的

1. **在 P1.1 完成前接 IBKR / 任何活券商。** 双账本、分数股、客户端报价会把 paper 分歧变成真钱分歧。README 已推迟，保持。
2. **不要让 ToolPlanner 注册下单工具。** 今天 plan 与 execute 分离是对的。
3. **不要把 `apply_adopted` 直接接到经纪。** 它忽略洞察正文，会把采纳当万能钥匙。
4. **不要在闭环未通时做「更聪明的 LLM 交易」。** 先总线，再模型。
5. **不要新增第三条账本**（例如再写一份 equity JSON「给学习用」）。
6. **不要把 HEARTBEAT 半实现**（只更新时间戳不拉文档 / 只拉不执行）。要么完整，要么删除。

---

## 建议落地切片（便于开 PR，本审计不执行）

| 切片 | 内容 | 依赖 |
|---|---|---|
| S1 | P0.1 + P0.2 + 测试 1–2 | 无 |
| S2 | P0.3 + P0.4 + 测试 3–4 | 无 |
| S3 | P0.5 密钥轮换（运维，非代码） | 无 |
| S4 | P1.4 再平衡可见失败 + 费用同源 + 测试 7 | S1 |
| S5 | P1.2 采纳绑定 + 测试 3 的 CLI 面 | S2 |
| S6 | P1.3 learn→barbell 或改 README paper-month | S4 |
| S7 | P1.1 选定账本（A 或 B）+ P1.5 | S4 |
| S8 | P2 回放 as-of + 匹配 + 测试 10–11、14 | 可与 S5 并行 |
| S9 | P3 审计/SQLite/REPL/文档诚实化 | S7 之后较好 |
| S10 | 清 ISSUE_LOG 过时遗留、测试计数、stepfun 路径 | 随时 |

S1–S3 可以在不碰架构的情况下把「代码说谎」降下来。S7 是架构分界：做完之前，所有「自主收益代理」的对外表述都应降级为「带人工闸门的 paper 研究工具」。

---

## 完成定义（本轮建议何时算落地）

- 默认 Python 路径：非有限数字进不了账；吹掉的账户能平仓；已采纳洞察不能被同 id 改置信度；标的大小写一致。
- 对外文档：能力表、paper-month、Rust 边界、默认 db/现金与代码一致。
- 若仍提供 `--rust-broker`：用户能说清「这是哪个账户、现金多少、审计在哪、学习看不看得到」。
- `learn` 要么移动仓位配置，要么不再出现在「控制回路」叙述里。
- 没有新的活券商代码路径。
