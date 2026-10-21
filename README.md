# Revenue Agent

Research-only core for an auditable autonomous revenue agent.

The initial scope is deliberately paper-only:

- ingest timestamped events and treat social data as untrusted evidence;
- consume Moltbook posts through the local `moltbook_scraper` client;
- consume Yahoo Finance OHLCV and basic fundamentals through local `yfinance`;
- score opportunities with a heuristic baseline and a deterministic attention scorer;
- model El Nino, AI drawdown, and AGI acceleration as explicit hypotheses;
- produce long/short hedge proposals without broker connectivity;
- keep every claim tied to evidence and later outcome verification.

Live-capability interfaces exist but are disabled by default. No live orders,
broker credentials, wallet transfers, or investment advice are active.

## Rust Risk Gateway

The broker and risk boundary can run as a Rust stdin/stdout JSON-lines service.
Python remains responsible for research and portfolio intent; Rust owns paper
cash, positions, server quotes, idempotency, quote freshness, order limits,
human strategy authorization, and the paper-only mode gate.

Build and run it:

```powershell
cargo build --manifest-path rust/Cargo.toml
$env:REVENUE_GATEWAY_STATE="data/rust-gateway-state.json"
py -3 -m revenue_agent paper-order --rust-broker --symbol AMD --side buy --notional 500 --price 100 --strategy-id INSIGHT_ID
```

The Rust gateway is still paper-only. Live broker, uncovered shorting,
copy-trading, and external platform tokens are not implemented or enabled.

## Capability Policy

```text
AGENT_ENABLE_COPY_TRADING=false
AGENT_ENABLE_EXTERNAL_TOKENS=false
AGENT_ENABLE_LIVE_BROKER=false
AGENT_ENABLE_UNCOVERED_SHORT=false
AGENT_MAX_LEVERAGE=1.0
AGENT_REQUIRE_HUMAN_ADOPTION=true
```

Social popularity never determines position size. An event first becomes an
`Insight`; only a human adoption record with `human_id` and rationale can
promote it to a long-term strategy. Live execution additionally requires
server-authoritative prices, freshness checks, margin checks, and an audit record.

## Language Boundary

The current agent is intentionally Python-only because Scrapling, yfinance,
pandas, and future GAT work already live in that ecosystem. A Rust execution
service is a later option only when measured broker latency or crash isolation
justifies a separate process. See `skills/rust-engineering-standard/SKILL.md`.

## Data Adapters

```python
from revenue_agent import MoltbookEventSource, YahooFinanceSource, local_moltbook_client

client = local_moltbook_client(
    api_key="read-from-your-secret-store",
    scraper_root=r"D:\\1\\making_money\\moltbook_scraper",
)
events = MoltbookEventSource(client, limit=100).fetch()

yahoo = YahooFinanceSource(("AMD", "NVDA"), period="1mo")
bars = yahoo.fetch()
fundamentals = yahoo.fetch_fundamentals()
```

Moltbook requires `MOLTBOOK_API_KEY`. Do not place API keys in source files or
the audit ledger. Both adapters are read-only and do not place orders.

## Research Replay

The baseline pipeline aligns events with only prices observed at or before the
event timestamp, adds a small fundamental adjustment, and emits paper signals:

```python
from revenue_agent import AssetProfile, ResearchPipeline

pipeline = ResearchPipeline((
    AssetProfile("AMD", "semiconductors", ("Advanced Micro Devices",)),
    AssetProfile("NVDA", "ai_infrastructure", ("NVIDIA",)),
))
signals = pipeline.run(events, bars, fundamentals)
```

The rule scorer is a benchmark for a later learned relational scorer. Every
signal contains its evidence rationale and can be written to `AuditLedger`.

## Agent Runtime

The OpenCode LLM gateway (`LLMClient`) is used as a low-privilege planner only.
StepFun support was removed entirely and is never called.

```text
OpenCode LLM -> JSON tool plan -> allowlist validation -> deterministic tool -> audit
```

It does not calculate prices, risk, leverage, PnL, or final position sizes.
Those operations belong to Python tools and governance gates. The planner may
request at most eight registered tools per plan and cannot request an unknown
tool or execute arbitrary code.

The barbell strategy is exposed as a research tool named
`find_barbell_pairs`. It rejects strong positive correlation, permits useful
negative correlation, and never submits an order.

## One-Command REPL

The normal entry point is a small command-line REPL. Running `revenue-agent`
with no subcommand opens it directly. It uses a paper account
with **1000 EUR** by default and keeps the state in SQLite.

```powershell
$env:PYTHONPATH="src"
py -3 -m revenue_agent --db data/paper.db --initial-cash 1000 repl
```

After installing the project, the shorter form is:

```powershell
revenue-agent --db data/paper.db --initial-cash 1000
```

Inside the REPL:

```text
status
cycle AMD NVDA
insights
learn
order AMD buy 100 100 INSIGHT_ID
quit
```

Slash forms such as `/status` and `/learn` are also accepted. Simple Chinese
requests such as `查看状态`, `运行研究`, and `根据收益优化策略` map to the
same safe commands.

`learn` updates the bounded attack/defense weights from paper equity, return,
and drawdown. A losing or high-drawdown run shifts 5 percentage points toward
defense; a profitable low-drawdown run shifts 2.5 points toward attack. The
weights stay between 25% and 75%, so the learner cannot make an uncontrolled
allocation jump.

## One-Month Paper Run

For a continuously running month-long simulation:

```powershell
py -3 -m revenue_agent --db data/paper.db --initial-cash 1000 paper-month `
  --duration-days 30 --interval-seconds 86400 --symbols AMD NVDA --moltbook
```

For a single smoke cycle with learning enabled:

```powershell
py -3 -m revenue_agent --db data/paper.db --initial-cash 1000 paper-month --once --symbols AMD NVDA
```

The month runner remains paper-only. It does not turn an LLM response directly
into an order; a strategy must still have a human adoption record.

## Other Commands

From the project directory:

```powershell
$env:PYTHONPATH="src"
py -3 -m revenue_agent status
py -3 -m revenue_agent cycle --symbols AMD NVDA
py -3 -m revenue_agent cycle --symbols AMD NVDA --moltbook --moltbook-max-posts 20
py -3 -m revenue_agent insight --insight-id INSIGHT_ID
py -3 -m revenue_agent adopt --insight-id INSIGHT_ID --human-id owner --rationale "Reviewed evidence and approved for paper trading."
py -3 -m revenue_agent replay --symbols AMD NVDA
py -3 -m revenue_agent rebalance --attack-symbol AMD --defense-symbol TLT --strategy-id INSIGHT_ID --price AMD=100 --price TLT=100
py -3 -m revenue_agent schedule --interval-seconds 1800
py -3 -m revenue_agent paper-order --symbol AMD --side buy --notional 1000 --price 100 --strategy-id INSIGHT_ID
py -3 -m revenue_agent mark --price AMD=105
```

Add `--moltbook` only after setting `MOLTBOOK_API_KEY`. A cycle stores Yahoo
data and produces insights, but insight adoption remains a separate human step.

## Postponed: IBKR

IBKR is intentionally a **one-month follow-up task**, not part of the current
simulation. Before connecting it, we will review the month's paper results,
verify the learner's behavior, and complete the IBKR paper API integration,
reconciliation, rate-limit handling, and kill-switch tests. Only after those
checks will live broker permissions be considered.

`MOLTBOOK_FOCUS_X_ACCOUNTS=aleabitoreddit` prioritizes matching handles in the
Moltbook research batch. It does not imply that the X account is a Moltbook
agent or that the agent should follow it on Moltbook.
