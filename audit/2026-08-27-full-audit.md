# Revenue Agent 全仓架构审计报告

- **日期**：2026-08-27
- **范围**：`src/revenue_agent`（Python）、`rust/crates/revenue-gateway`（Rust）、CLI/REPL、测试、README / ISSUE_LOG / HEARTBEAT
- **性质**：只读审计。本文件是产出，不附带代码修改。
- **对照**：2026-08-22 `ISSUE_LOG.md` 全库修复记录
- **探针**（独立进程，未改仓库）：
  - `AGENT_MAX_LEVERAGE=nan/inf` → `require_leverage(1e9)` 放行
  - 非正权益全平 → `PermissionError`（文案声称允许减仓）
  - 同内容不同 confidence → 同一 insight id，已采纳行被 `INSERT OR REPLACE` 覆盖
  - `"The AI boom continues"` → 规则流水线同时给 `AI` 和 `ON` 发多头
  - 常数价格序列 → `find_weakly_correlated_pairs` 接受为弱相关对
  - `price=nan` 下单 → 校验放过，SQLite `NOT NULL` 才挡住

---

## 1. 结论

2026-08-22 那一轮 ISSUE_LOG 修复大部分还在，局部风控比当时更硬。真正的问题已经从「单点 bug」变成「系统没闭环」。

研究、采纳、下单、盯市、学习、再平衡、Rust 账本是六块能单独跑通、合在一起却对不齐的子系统。

- 默认 Python 路径是认真的 paper 核心：无活券商、无 LLM 直接下单、CLI 强制采纳、空头默认关。
- `--rust-broker`、`learn`、`paper-month` 和 README 能力表会让人误以为系统在做它并没有做的事。
- 作为「可审计的自主收益代理」，当前是一排没有总线的模块：上一轮修的是闸门有没有装上；这一轮的核心是闸门装在了两扇不同的门上。

---

## 2. 系统画像

| 层 | 实际职责 | 是否接到交易 |
|---|---|---|
| Yahoo / Moltbook / Scrapling | 取数 | cycle 用 Yahoo+Moltbook；Scrapling 只在测试里 |
| `InsightGenerator` + OpenCode | 从社交文本生成洞察 | 入库，不下单 |
| `ResearchPipeline` / Replay | 关键词 + 事前收益 → `PaperSignal` | CLI `replay` 只打印，不下单 |
| `adopt` | SQLite 写一条 human 记录 | 只校验 id 存在 |
| `PaperBroker` + SQLite | 真正的默认账本 | CLI/REPL Python 下单走这里 |
| Rust gateway | 另一套默认 10 万现金的 JSON 账本 | `--rust-broker` 才走，和 SQLite 不通 |
| `learning_state` | 把权益窗口收成 25–75% 攻防权重 | 没有任何交易路径读取 |
| `rebalance` / barbell | 按 CLI 手填拥挤度/估值调仓 | 不读学习结果，不读洞察方向 |
| `AuditLedger` JSONL | Python 路径两阶段意图/结果 | Rust 路径不写 |
| `HEARTBEAT.md` | 每 30 分钟拉 Moltbook heartbeat | 代码里没有实现 |

README 暗示的回路：

```text
证据 → 洞察 → 人工采纳 → 下单/再平衡 → 盯市 → 学习 → 调整攻防权重 → 再下单
```

代码里实际是：

```text
cycle:       Yahoo 入库 +（可选）Moltbook→LLM 洞察 + 尝试盯市
adopt:       只存 insight_id
paper-order / rebalance: 只要 id 被采纳，标的/方向/规模任意
learn / paper-month: 改 learning_state 并打印
rebalance:   永远 BarbellConfig(0.5/0.5)，不读 learning_state
paper-month: 只 cycle+learn，30 天也可以一笔单都不下
```

这不是小功能缺失，是产品语义和代码语义不一致。

---

## 3. 上一轮 ISSUE_LOG 验收

| 编号 | 声明 | 现状 |
|---|---|---|
| #1 默认禁止裸空 + 杠杆帽 | Python `PaperBroker` 已接 `CapabilityGate` | **部分成立**。空头/超卖默认拒绝；非正权益时减仓也被拒（与修复文案相反）；`nan`/`inf` 杠杆帽失效 |
| #2 paper-order 必须采纳 | argparse `required=True` + `has_adoption` | **成立**（CLI/REPL Python 路径） |
| #3 洞察 ID 不信 LLM | 内容哈希 24 hex | **部分成立**。`confidence` 不进哈希，已采纳行仍可被 REPLACE |
| #4 ToolPlanner 双路径校验 | `_validated_calls` 共用 | **基本成立**。原生路径把非 dict 参数静默变成 `{}` |
| #5 部分平仓均价 | 三态合并 | **成立**（Python/Rust 同构，尘埃阈值不同） |
| #6 mark 炸调度 | cycle `mark_skipped`；scheduler 吞 Exception | **成立**。长期任务仍 exit 0 |
| #7 learn 幂等 | `last_mark_at` 窗口 | **成立**。旧库 ALTER 后 `NULL` 会再吃一遍全历史 |
| #8 Rust 杠杆 + snapshot 成本回退 | 已实现 | **成立于库内**。进程不读 `AGENT_*`；成本回退进入杠杆公式 |
| #9 Moltbook 时间戳 | 不可解析则丢弃 | **成立**。Scrapling 仍用 `now()` |
| #10 调度节拍 | 完成后等待，无 catch-up | **成立**。pytest 未覆盖间隔，ISSUE_LOG 引用仓外探针 |
| #11 洞察最多 3 条 | `MAX_INSIGHTS_PER_RESPONSE` | **成立** |
| #12 网关 state 文件锁 | `create_new` + 30s 过期 | **名义存在、排他性不成立**（见 H10） |
| #13 两阶段审计 | intent 然后 result | **仅 Python 下单/再平衡**。Rust CLI 不写；adopt/cycle 仍先落库后审计 |
| R1 再平衡费用预留 | `_reserve_fee_budget` | **部分成立**。规划 15bps vs 成交 10bps；测试允许 `total == 100_000`，没有预留也会绿 |
| R4 依赖 / 去 StepFun | `requests`+`yfinance` 已声明 | ISSUE_LOG 遗留仍写 `dependencies=[]`，过时 |
| R5 超时+重试 | config 默认 240s | `LLMClient.__init__` 默认仍 60s |
| #14 `.env` 明文密钥 | `.gitignore` 忽略 `.env` | **仍成立**。磁盘 `.env` 有可用的 OpenCode / Moltbook key，应轮换（本报告不写出具体值） |

ISSUE_LOG 页眉仍写「52 通过」；R5 之后仓库是 55 个 Python 测试 + 6 个 Rust 测试。

---

## 4. 高严重度

编号供建议文档交叉引用。行号以 2026-08-27 工作区为准。

### H1. 两套 paper 账本

Python：`--db`（默认 `data/agent.db`）、`--initial-cash` 1000、`AuditLedger`、`RuntimeCapabilities.from_env()`。

Rust：`REVENUE_GATEWAY_STATE` 默认 `data/rust-gateway-state.json`、现金 **100_000**、`max_order_notional=25_000`、忽略 `--initial-cash` 和全部 `AGENT_*`。`main.rs` 加载状态时永远 `RiskConfig::default()`。

`status` / `learn` / `mark` / paper-month 只碰 SQLite。Rust 成交对它们不可见。同一条 CLI 看起来在管一个账户，实际可能在另一个账户成交。

位置：`src/revenue_agent/cli.py`（`_paper_order` / `_rebalance` 的 rust 分支）、`rust/crates/revenue-gateway/src/main.rs`（`load_gateway`）。

### H2. 同一 CLI `--side sell` 在两条路径上不是同一指令

Python 在 `enable_uncovered_short` 时允许 `sell` 穿零变空（`tests/test_paper_broker.py::test_flip_through_zero_reprices_at_trade`）。

Rust **永远**拒绝超量 `Sell`，空头只能走 `Side::Short`，而二进制里 `allow_uncovered_short` 恒为 false。

打开环境变量后：Python 能空、Rust 不能。关掉时两边都拒 `short`，但 `sell` 语义仍不同。

位置：`paper_broker.py:58-59`；`lib.rs:237-239, 276-280`。

### H3. 非正权益无法减仓（修复承诺未落地）

ISSUE_LOG #1 与报错文案都写「权益非正时仅允许减仓」。代码无条件 `raise`：

```text
paper_broker.py  _require_leverage_within_cap
  if equity <= _EPSILON: raise PermissionError(...)
  self.gate.require_leverage(gross / equity)
```

没有 `projected_gross > old_gross` 判断。探针：把现金改到权益为负后全平，同一 `PermissionError`。

后果：收紧 `AGENT_MAX_LEVERAGE` 之后，Python 会锁死需要卖掉才能回到帽内的仓。Rust 只在敞口上升时检查，纯减仓放行。

亏钱空头的 cover 同样会被「剩余仓按成交价重估后权益为负」挡住，小额止损无法执行。

### H4. `AGENT_MAX_LEVERAGE=nan` / `inf` 杠杆帽失效

`from_env` 只做 `float(...)`。`CapabilityGate` 只拒绝 `< 1.0`。IEEE：`nan < 1` 为假，`x > nan` 为假，`x > inf` 为假。

探针：`require_leverage(1e9)` 在 nan/inf 下都通过。失败开放。

同类：`notional`/`price` 校验是 `<= 0`。`nan` 和 `inf` 都不是 `<= 0`。`price=nan` 被 SQLite NOT NULL 挡住（丑陋）；`notional=inf` 的卖单 `cash_delta = inf - inf` 变成 NaN，之后现金闸门全部失效。Rust 进程内 API 同样：`NaN <= 0` 与 `NaN > 25000` 均为假。JSON 协议因 serde 拒 NaN 相对安全。

位置：`governance.py:32, 55-57`；`paper_broker.py:40-41, 90-97`；`lib.rs:231-236`。

### H5. 已采纳洞察仍可被同 ID 覆盖；采纳是令牌不是策略

内容哈希不含 `confidence`。同题/同方向/同证据、不同置信度 → 同一 24 位 id → `save_insight` 的 `INSERT OR REPLACE` 改掉已采纳行，`strategy_adoptions` 仍指向它。探针：0.2 被覆盖成 0.99。

采纳不绑定标的、方向、`invalid_if`。AMD-long 的 id 可以拿去 `short NVDA`。`PaperBroker.submit_market_order` 的 `strategy_id` 可空，库路径没有采纳闸。`AgentRuntime.apply_adopted` 只检查 id，然后对全部事件跑规则流水线。

位置：`insights.py:102-108`；`storage.py:110-115, 124-130`；`paper_broker.py:37`；`runtime.py:31-33`。

### H6. `learn` 不改变交易；`paper-month` 不下单

`update_strategy_from_equity` 写入 `attack_weight` / `defense_weight`。`rebalance_barbell` 永远从 `BarbellConfig(0.5, 0.5)` 起步。`_paper_month` 只 `_cycle` + learn。

文档中的「根据收益优化策略」在执行层是空操作。一个月仿真按代码是定时研究爬虫 + 对空仓盯市。

位置：`learning.py:49-56`；`barbell.py:29-32, 97-108`；`cli.py:314-341`。

### H7. 符号大小写导致静默跳腿 / 重复换仓

- `--price amd=100` → 键 `AMD`（`_key_value` upper）。
- `--symbol` / `--attack-symbol` **不**规范化。
- Python 持仓精确匹配：`amd` 持仓 + `AMD` 报价 → mark 失败。
- 再平衡 `prices.get(symbol)` 取不到就 `continue`，腿被扔掉，CLI 仍 exit 0。
- Rust 把 symbol upper。攻击腿写成 `amd`、持仓是 `AMD` 时，计划层看成两个名字。

位置：`cli.py:428-432`；`rebalancer.py:30-33`；`lib.rs:183, 240`。

### H8. `rebalance --rust-broker` 被 25k 单笔上限吃空

空仓 50/50 × 100k 现金 → 每腿约 50k > `max_order_notional` 25000。`execute_barbell_orders` 吞掉 `RuntimeError`，返回空 fills，CLI 仍打印成功形态。

位置：`lib.rs:231-236`；`rebalancer.py:60-69`；`cli.py:260-277`。

### H9. 客户端把 `--price` 写成「服务器报价」，并丢掉幂等

`RustGatewayClient.submit_market_order` 先 `set_quote(symbol, price)` 再 submit，新鲜度检查验证的是自己刚写入的价格。每次 `uuid4()`，网关幂等键对 CLI 重试无效，重试双花。

README / `skills/rust-engineering-standard/SKILL.md` 的 server-authoritative prices 与 idempotency 在这条路径上不成立。

位置：`rust_gateway.py:54-57`。

### H10. Rust 锁文件不是锁；坏 JSON 失败开放成新账本

`acquire_state_lock`：`create_new` 写出 PID 后立刻丢掉 `File` 句柄，进程生命周期没有持有的 OS 锁。mtime 只在 `save_gateway` 时刷新；`snapshot` / `health` 不写盘。stdin 阻塞的活进程 30 秒后会被当成崩溃接管。被抢走后，原进程 `Drop` 仍 `remove_file`，把后来者的锁删掉。等待约 12 秒、过期 30 秒：刚崩溃立刻重启会 exit 2 并把锁留下。

`load_gateway`：`read.ok().and_then(parse.ok())`，权限错误、截断、缺字段全部变成「无状态」→ 新开 100_000 现金。下一次成功 save 把清空写回磁盘。`PersistedState` 无版本号。`save_gateway` 的 write/rename 错误被 `let _ =` 吞掉，无 fsync。

协议测试从未争用锁。ISSUE_LOG #12「含锁获取/释放路径」过满。

位置：`main.rs:104-165, 120-150`。

### H11. 成本回退进入杠杆公式（不只是展示）

Rust 对非本笔持仓用 `reference_price`（过期则均价）做 `projected_gross`。空头亏了、报价过期：负债低估、权益高估，仍允许再加风险单。Python 对其它名字同样用 `average_price`，docstring 称 conservative，对亏损空头恰恰相反。

位置：`lib.rs:194-202, 283-301, 384-407`；`paper_broker.py:75-88`。

---

## 5. 中严重度

### 执行 / 风控

- `cover` 就是 `buy`：没有空头也能 cover 成多；超量 cover 会穿零翻多。
- Python 无单笔 notional 上限；Rust 有 25k。
- `snapshot()` 会写入 `paper_equity`。Python `rebalance` 规划一次就多一个学习点，即使随后所有腿被跳过。
- 其它持仓缺价：Python mark/rebalance 直接抛；Rust 按成本计价并列出 `unpriced_symbols`，再平衡权重可能漏掉这些名字，把它们的资本当成可调仓现金。
- `execute_barbell_orders` 吞 `ValueError` / `PermissionError` / `RuntimeError`。跳过卖、执行买 → 进攻腿还在、防御腿加仓。CLI 仍写 `barbell_rebalance_result` 并返回 0。
- `rebalance_barbell` 对 `current_weights` 里非攻防标的目标为 0。持有 NVDA 再平衡 AMD/TLT 会卖 NVDA。
- 生产 `max_turnover=0.25`；演示 25/75 切换的测试把 turnover 放到 1.0。线上一次调不到测试里的目标。
- 费用：规划 15bps、成交 10bps、研究 `execution_cost=0.001`。`transaction_cost_bps=0` 时预留提前返回，R1 会以相反方向再现。
- `fee_bps > 10000` 合法，卖单 `notional - fee` 为负且无现金下限。
- `LongTermStrategyRegistry` 是进程内字典；CLI 用完即扔。SQLite 才是真相源。`AGENT_REQUIRE_HUMAN_ADOPTION` 在 CLI 下单路径上是死变量（下单永远查 SQLite，反而更安全）。
- 直接对网关 stdin `authorize` 任意 `strategy_id`+`human_id` 即可。SQLite 撤销不会清 JSON 里的授权图。
- 关闭对冲腿会降低 gross，Rust 把它当「减风险」跳过杠杆帽，净风险可能上升。

### 研究 / 回放（前瞻与数据诚信）

- Yahoo 日线时间戳通常是交易日 00:00（交易所时区），`Close` 是收盘价。`bar.observed_at <= event.observed_at` 会把**当日收盘**算进事件发生前的收益。测试只用 t-1 vs t+1。这是回测文献里最常见的 look-ahead 之一。
- `fetch_fundamentals` 盖的是抓取时刻 `now()`，不是财报发布日；yfinance 是重述后序列，不是 point-in-time。表 `fundamentals` 主键只有 `symbol`，每次 cycle `REPLACE`，没有历史。
- `ResearchPipeline.run` 不过滤基本面时点；只有 `ReplayRunner` 过滤。CLI `replay` 往往因为 `now() > event` 而完全丢掉基本面调整。
- Moltbook 按 `created_at` 打事件时间，正文却是抓取时版本；未来时间戳会让 `_prior_return` 吃进全部 K 线。`max_posts` 先截断再按 focus 排序，焦点账号不在前 20 条里就会消失。`posts is None` 会 TypeError。Unix epoch 时间戳被当无效丢掉。
- Scrapling 仍用 `now()` 当事件时间（#9 同类），CLI 未使用。`allowed_domains` 只检查构造 URL，不检查重定向后主机。
- `name.casefold() in haystack` 是子串。探针：*The AI boom continues* 同时匹配 **AI** 和 **ON**（`continues` 含 `on`）。词袋：`miss`∈`mission`，`beat`∈`heartbeat`。`profile.symbol in event.entities` 大小写敏感，正文路径不敏感。
- 常数价格 Pearson=0，会被当成合格对冲对。`min_observations` 数的是收益率根数（`len(timestamps)-1`），20 根 K 线凑不满 20。
- NaN 分数：`abs(nan) < 0.05` 为假，`nan > 0` 为假 → 方向变成 **short**、权重 nan。Yahoo `float(Close)` 不丢 NaN。
- `_prior_return` 检查全历史第一根 close≠0，除的是窗口第一根；窗口里出现 0 会 `ZeroDivisionError` 打爆 cycle。
- `auto_adjust=False` 时拆面会变成巨大「事前收益」。
- `runtime.research` 丢掉 bars/fundamentals，洞察看不到行情。
- 每个事件×品种可发 `weight ≤ 0.2`。cycle 当前不下单所以暂时安全；若有人把 `PaperSignal.weight` 按事件求和，匹配帖数量就是规模。与 README「社交热度永不决定仓位」冲突。

### LLM 边界

id 已改为服务端哈希（#3 主修复成立）。其余字段仍信模型：

- `affected_entities` 若是字符串 `"AMD"`，会存成 `('A','M','D')`。
- 空 title/thesis 合法；prompt 禁止的 `up`/`bullish` 会被静默映射。
- 实体顺序进入哈希（未排序），证据 id 排了序。同一实体不同顺序 → 不同 id。
- 事件正文 6000 字进 prompt，只能靠「把 DATA 当不可信」一句话。引用必须是真实 evidence_id，但攻击帖可以劫持真实 id 上的 thesis/direction。
- `json_mode=False` 靠 `inspect.signature`；包装过的 `complete(self, messages, **kwargs)` 会退回默认 `json_mode=True`（当初 HTTP 500 的组合）。
- 重试最多约 3×240s；429/SSL 不重试。`models()` 无重试。
- Tool `arguments` 原样进 handler。`plan()` 本身不下单（成立）；CLI cycle 也不调用 planner。原生路径非 dict 参数变成 `{}`。

### 存储 / 审计 / 调度

- JSONL 哈希链无 fsync、无跨进程锁。两进程会叉链。崩溃截断最后一行会让 `__init__` 解析失败、账本卡死。链也防不了尾截断（需外锚或签名）。
- `_jsonable` 放过 NaN，`json.dumps(allow_nan=True)` 写出非法 JSON。
- `adopt` / `research_cycle` 仍先落库再审计。
- SQLite 无定制 `busy_timeout`、无 `foreign_keys=ON`。`strategy_adoptions` 无 FK。学习 RMW 无事务，并发 `/learn` 会把同一窗口加两遍。
- ALTER `last_mark_at` 吞掉所有 `OperationalError`。新库 CREATE 仍不含该列，靠 ALTER 补。升级后 `NULL` 会再学一遍全历史。
- `paper_equity` 以时间戳字符串为主键，同时刻 REPLACE；时钟回拨的新点若 `<= last_mark_at` 永不被学习消费。
- 窗口回撤覆盖存储的 `max_drawdown`（不是终身高水位）。权益为负时 DD 符号反了。`start==0` 时收益率强制 0。
- `schedule` / `paper-month` 回调失败仍 **exit 0**。daemon 线程。`run_once()` 不走 `_safe_callback`。
- `INSERT OR IGNORE paper_account`：库已存在时 `--initial-cash` 被忽略。
- 再采纳同一 insight 会 REPLACE human_id。
- `events` / `insights` 无 created_at；`paper_orders.strategy_id` 可空无 FK。
- `.gitignore` 未覆盖 `*.db-wal` / `*.db-shm`、`memory/`、`*.egg-info/`。

### REPL / 配置

- 自然语言子串：`bicycle`→cycle，`learning`→learn。
- `/order` 必须恰好 6 段，少一段掉进 status/cycle 而不是报错。
- REPL 没有 adopt / rebalance / mark；文档示例却在 REPL 里 `order … INSIGHT_ID`。
- REPL 只要环境里有非空 `MOLTBOOK_API_KEY` 就自动 `--moltbook`；CLI 必须显式 flag。动态 `exec_module` `{cwd}/../moltbook_scraper/src/client.py`。
- 文档默认库 `data/paper.db`，代码默认 `data/agent.db`。
- README `paper-order --notional 1000` 在 1000 EUR + 10bps 下现金不够（需要 1001）。
- `LLMClient` 默认 timeout 60 vs config 240。
- `load_dotenv` 只读 CWD；行内 `#` 会进环境变量值。
- `/config` 不打印密钥（好）。异常字符串一般含 URL 不含 Authorization。

---

## 6. Python vs Rust 行为对照

| 主题 | Python PaperBroker | Rust gateway |
|---|---|---|
| 初始现金 | CLI 默认 1000 | 100_000 |
| 单笔上限 | 无 | 25_000 |
| 符号 | 原始字符串 | ASCII upper |
| 价格来源 | 调用方 `price` | 存储报价；Python 客户端提交前覆盖 |
| 新鲜度 | 无 | 60s |
| 幂等 | 无 | 按 key；客户端每次新 UUID |
| `sell` > 持仓 | 开了空头开关则允许穿零 | 永远 `uncovered_sell` |
| 杠杆何时检查 | 每笔，含减仓 | 仅 projected gross 上升 |
| 非正权益减仓 | 拒绝 | 允许 |
| 其它持仓计价 | 成本均价 | 新报价否则成本 |
| 缺价 | mark 抛错 | snapshot 成本 + `unpriced_symbols` |
| 费用 | `fee_bps` 默认 10 | 常量 10 |
| 持久化 | SQLite `BEGIN IMMEDIATE` | JSON tmp+rename，错误忽略 |
| 审计 | Python 路径两阶段 JSONL | rust CLI 路径不写 |
| 环境能力 | `from_env()` 空头+杠杆 | 忽略 `AGENT_*` |
| 采纳 | CLI 查 SQLite | 网关 HashMap，stdin 可信 |
| 提交返回 | `int` order_id | `dict` fill |
| 尘埃平仓 | `abs(qty) < 1e-9` | `f64::EPSILON` |

`execute_barbell_orders` 类型标注为 `PaperBroker`，CLI rust 路径传入 `RustGatewayClient`（鸭子类型）。fills 变成 dict 而非 int。

SKILL 要求：market-data、order、**cancel**、fill、position、health；服务器报价；显式 live/short/leverage 配置。实际协议：`Health \| Quote \| Authorize \| Submit \| Snapshot \| Shutdown`。无 cancel。health 不看状态。

---

## 7. 文档 / 代码冲突

1. README「当前是 Python-only，Rust 是以后的选项」vs 已经接上的 `--rust-broker`。
2. 能力表六条环境变量 vs 真正进下单的只有空头开关和杠杆。
3. 「Rust 拥有服务器报价」vs Python 客户端用用户 `--price` 灌 quote。
4. skill.md 要求 cancel / market-data / 能力开关进网关 vs 实际协议。
5. 「一个月仿真必须有采纳才能下单」vs `paper-month` 根本不下单。
6. 「社交热度永不决定仓位」vs 若对 `PaperSignal.weight` 求和，匹配帖数量就是规模。
7. ISSUE_LOG 仍写 `dependencies=[]`、52 个测试、R2 指向已删除的 `stepfun_client.py`。
8. `HEARTBEAT.md` 与 `memory/heartbeat-state.json`（停在 2026-08-08）没有任何实现。
9. `pyproject` 未声明 `scrapling`；README 把 pandas/Scrapling 写成选型理由，源码几乎不用 pandas。
10. README 再平衡示例省略 `adopt`，代码会 `PermissionError`。
11. `build_hedged_book` docstring「负权重表示空头」，实现是 `direction="short"` + 正权重。
12.  barbell 工具「从不提交订单」对 `find_barbell_pairs` 为真；CLI `rebalance` 会提交。

---

## 8. 死代码与未接线能力

未接线、只在测试或导出里出现：

- `BrokerAdapter` / `CopyTradingAdapter` / `ExternalPlatformAdapter`（空 Protocol）
- `default_scenarios` / `build_hedged_book` / `scenario_impact`
- `ScraplingEventSource`
- `register_barbell_tools`
- `AgentRuntime.plan` / `execute_plan` / `apply_adopted`
- `HeuristicScorer` 的返回值（算了就扔）
- `domain.StrategyActivation`（与 `StrategyAdoption` 重复）
- `HEARTBEAT.md` / `memory/heartbeat-state.json`
- `require_live_broker` / `require_copy_trading` / `require_external_token`（仅测试调用）
- `paper_orders` 除 `COUNT(*)` 外无读者
- `insights.direction` / `affected_entities` 展示用，不约束订单
- `learning_state.attack_weight` 只打印

Barbell 目标权重永远为正 0.25–0.75，不会表达 insight 的 short。

---

## 9. 测试缺口

55 个 Python 测试覆盖了上一轮的主要回归，但有几处名字过满、断言过空。

| 测试 | 问题 |
|---|---|
| `protocol.rs` 未授权 submit | 响应被丢掉，从未 assert `strategy_not_adopted` |
| `test_rebalance_sells_execute_before_buys` | 只断言计划顺序；成交后不查仓；`execute` 吞异常 |
| `test_barbell_orders_are_weight_deltas` | `total <= 100_000` 在没有预留时也成立 |
| 配对测试 | 只给一对攻防，删掉相关性过滤也会过 |
| 洞察哈希 | 不经 `save_insight`、不测 confidence、不测采纳后覆盖 |
| `test_cli._seed_insight` | 仍写入攻击者自选 id |
| 杠杆测试 | 不测减仓放行、不测 nan/inf、不测亏钱空头 cover |
| `RustGatewayClient` | **零测试** |
| 其它缺失 | 符号大小写、采纳绑定标的、learn→rebalance、paper-month 零成交、REPL 子串、审计并发、SQLite 基本面主键、锁争用、坏 JSON 加载、非正权益减仓 |

`test_paper_broker_persists_orders_positions_and_marks` 只查 `equity > 1000`。`test_engine` 的「不可信事件不能报高可靠度」只拒 1.1，社交 0.99 合法。

---

## 10. 安全与密钥（不写具体值）

- `.env` 在磁盘上，含非空 `OPENCODE_GO_API_KEY` 与 `MOLTBOOK_API_KEY`。`.gitignore` 已忽略 `.env`。项目尚未 `git init`。
- 测试与 `data/*.json` 未见密钥。
- ISSUE_LOG #14：StepFun key 已随 R4 从 `.env` 移除（仍应在 StepFun 控制台吊销，若尚未吊销）；OpenCode / Moltbook 仍明文。
- 建议：轮换这两把 key；未来用系统密钥库；`git init` 前确认 ignore。

---

## 11. 命令 × 闸门矩阵

| 命令 | 下单？ | 人工采纳 | 空头/杠杆 | 两阶段审计 | 失败退出码 |
|---|---|---|---|---|---|
| `paper-order`（Python） | 是 | 是（required + has_adoption） | `from_env()` | 是 | 未捕获 → 1 + traceback |
| `paper-order --rust-broker` | 是（另一账本） | 是（adoption_human_id） | 仅 Rust 默认；Python env 忽略 | **否** | RuntimeError → 1 |
| REPL `/order` | 是（rust_broker=False） | 同 Python paper-order | `from_env()` | 是 | 打印；进程 exit 0 |
| `rebalance`（Python） | 是 | 是 | `from_env()`；跳过的腿被吞 | 意图/结果总会写 | **总是 0** |
| `rebalance --rust-broker` | 是（Rust 账本） | 是 | Rust 默认 + 25k 帽 | **否** | 腿内吞掉则 0 |
| `cycle` | 否（研究+可选 mark） | 不要求 | mark 而已 | `research_cycle` | 0，或取数/LLM 抛 1 |
| `schedule` | 否（包 `_cycle`） | n/a | n/a | 同 cycle | **0**（除非 `--once`） |
| `paper-month` | **否** | 未使用 | n/a | 同 cycle | **0** |
| `learn` / REPL `/learn` | 否 | n/a | n/a | 无 | REPL 打印 |
| 库 `PaperBroker` | 是 | **否** | 默认安全档，除非调用方传入 | 否 | raise |
| 库 `RustGatewayClient.authorize` | 随后可下单 | 自助任意 human_id | Rust 默认 | 否 | RuntimeError |

没有路径能靠环境变量拼写错误打开活券商（活券商实现不存在，Rust `Mode::Live` 硬拒）。这是当前最重要的安全底线，应当保持。

---

## 12. 业界对照（改进方向的依据，不是功能清单）

对照材料包括：Python/Rust 混合交易的「研究离线、执行唯一」分层、XQRiskCore 式预交易批准 + 事后监控、risk-gated-execution（LLM 只出意图、闸门在执行层）、哈希链审计的单写者/fsync/尾截断限制、以及回测的前瞻偏差 / 存活者偏差 / 财报重述偏差文献。

对本仓库已声明目标（可审计、人工采纳、paper-only、研究与执行分离）意味着：

1. 执行层只能有一个账本真相源。
2. 采纳必须约束标的、方向、规模，而不能只是 id 令牌。
3. 日线回放必须用上一交易日收盘，基本面必须按发布日且保留历史。
4. JSONL 哈希链要单写者 + fsync；无签名时只能防误改，不能防持有写权限的人重写历史。
5. 学习若不影响配置，就不要叫控制回路。
6. IBKR 之前必须先对账；双账本和客户端报价还在时接券商，会把 paper 分歧变成真钱分歧。

具体改法见同目录 `2026-08-27-recommendations.md`。
