from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .domain import Event, FundamentalSnapshot, Insight, MarketBar


class SQLiteStore:
    """Durable local state for replay, paper trading, and audit inspection."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def close(self) -> None:
        self.connection.close()

    def _init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, source TEXT NOT NULL,
                title TEXT NOT NULL, body TEXT NOT NULL, entities_json TEXT NOT NULL,
                source_reliability REAL NOT NULL, url TEXT
            );
            CREATE TABLE IF NOT EXISTS market_bars (
                symbol TEXT NOT NULL, observed_at TEXT NOT NULL, open REAL NOT NULL,
                high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
                volume REAL NOT NULL, source TEXT NOT NULL, PRIMARY KEY(symbol, observed_at)
            );
            CREATE TABLE IF NOT EXISTS fundamentals (
                symbol TEXT PRIMARY KEY, observed_at TEXT NOT NULL, market_cap REAL,
                revenue_growth REAL, profit_margins REAL, operating_margins REAL,
                trailing_pe REAL, source TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS insights (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, thesis TEXT NOT NULL,
                affected_entities_json TEXT NOT NULL, horizon TEXT NOT NULL,
                confidence REAL NOT NULL, evidence_ids_json TEXT NOT NULL,
                direction TEXT NOT NULL, invalid_if TEXT
            );
            CREATE TABLE IF NOT EXISTS strategy_adoptions (
                insight_id TEXT PRIMARY KEY, human_id TEXT NOT NULL,
                adopted_at TEXT NOT NULL, rationale TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
                side TEXT NOT NULL, quantity REAL NOT NULL, price REAL NOT NULL,
                notional REAL NOT NULL, fee REAL NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, strategy_id TEXT
            );
            CREATE TABLE IF NOT EXISTS paper_positions (
                symbol TEXT PRIMARY KEY, quantity REAL NOT NULL,
                average_price REAL NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_account (
                id INTEGER PRIMARY KEY CHECK(id = 1), cash REAL NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_equity (
                observed_at TEXT PRIMARY KEY, cash REAL NOT NULL,
                market_value REAL NOT NULL, equity REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learning_state (
                id INTEGER PRIMARY KEY CHECK(id = 1), attack_weight REAL NOT NULL,
                defense_weight REAL NOT NULL, observations INTEGER NOT NULL,
                last_return REAL NOT NULL, max_drawdown REAL NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        # Lightweight in-place migration for databases created before the
        # incremental-learning window column existed.
        try:
            self.connection.execute("ALTER TABLE learning_state ADD COLUMN last_mark_at TEXT")
        except sqlite3.OperationalError:
            pass
        self.connection.commit()

    def save_events(self, events: tuple[Event, ...]) -> None:
        self.connection.executemany(
            "INSERT OR REPLACE INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(e.id, _iso(e.observed_at), e.source, e.title, e.body, json.dumps(e.entities), e.source_reliability, e.url) for e in events],
        )
        self.connection.commit()

    def save_bars(self, bars: tuple[MarketBar, ...]) -> None:
        self.connection.executemany(
            "INSERT OR REPLACE INTO market_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(b.symbol, _iso(b.observed_at), b.open, b.high, b.low, b.close, b.volume, b.source) for b in bars],
        )
        self.connection.commit()

    def save_fundamentals(self, snapshots: tuple[FundamentalSnapshot, ...]) -> None:
        self.connection.executemany(
            "INSERT OR REPLACE INTO fundamentals VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(f.symbol, _iso(f.observed_at), f.market_cap, f.revenue_growth, f.profit_margins, f.operating_margins, f.trailing_pe, f.source) for f in snapshots],
        )
        self.connection.commit()

    def save_insight(self, insight: Insight) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO insights VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (insight.id, insight.title, insight.thesis, json.dumps(insight.affected_entities), insight.horizon, insight.confidence, json.dumps(insight.evidence_ids), insight.direction, insight.invalid_if),
        )
        self.connection.commit()

    def save_adoption(self, adoption: Any) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO strategy_adoptions VALUES (?, ?, ?, ?)",
            (adoption.insight_id, adoption.human_id, _iso(adoption.adopted_at), adoption.rationale),
        )
        self.connection.commit()

    def has_adoption(self, insight_id: str) -> bool:
        row = self.connection.execute("SELECT 1 FROM strategy_adoptions WHERE insight_id = ?", (insight_id,)).fetchone()
        return row is not None

    def adoption_human_id(self, insight_id: str) -> str | None:
        row = self.connection.execute("SELECT human_id FROM strategy_adoptions WHERE insight_id = ?", (insight_id,)).fetchone()
        return str(row[0]) if row else None

    def get_insight(self, insight_id: str) -> Insight | None:
        row = self.connection.execute("SELECT * FROM insights WHERE id = ?", (insight_id,)).fetchone()
        if row is None:
            return None
        return Insight(
            id=row["id"], title=row["title"], thesis=row["thesis"],
            affected_entities=tuple(json.loads(row["affected_entities_json"])), horizon=row["horizon"],
            confidence=row["confidence"], evidence_ids=tuple(json.loads(row["evidence_ids_json"])),
            direction=row["direction"], invalid_if=row["invalid_if"],
        )

    def load_events(self) -> tuple[Event, ...]:
        rows = self.connection.execute("SELECT * FROM events ORDER BY observed_at, id").fetchall()
        return tuple(Event(row["id"], _parse_time(row["observed_at"]), row["source"], row["title"], row["body"], tuple(json.loads(row["entities_json"])), row["source_reliability"], row["url"]) for row in rows)

    def load_bars(self) -> tuple[MarketBar, ...]:
        rows = self.connection.execute("SELECT * FROM market_bars ORDER BY observed_at, symbol").fetchall()
        return tuple(MarketBar(row["symbol"], _parse_time(row["observed_at"]), row["open"], row["high"], row["low"], row["close"], row["volume"], row["source"]) for row in rows)

    def load_fundamentals(self) -> tuple[FundamentalSnapshot, ...]:
        rows = self.connection.execute("SELECT * FROM fundamentals ORDER BY observed_at, symbol").fetchall()
        return tuple(FundamentalSnapshot(row["symbol"], _parse_time(row["observed_at"]), row["market_cap"], row["revenue_growth"], row["profit_margins"], row["operating_margins"], row["trailing_pe"], row["source"]) for row in rows)

    def equity_history(self) -> tuple[tuple[datetime, float], ...]:
        rows = self.connection.execute("SELECT observed_at, equity FROM paper_equity ORDER BY observed_at").fetchall()
        return tuple((_parse_time(row["observed_at"]), float(row["equity"])) for row in rows)

    def learning_state(self) -> dict[str, float | int | str | None] | None:
        row = self.connection.execute("SELECT * FROM learning_state WHERE id = 1").fetchone()
        if row is None:
            return None
        return {"attack_weight": row["attack_weight"], "defense_weight": row["defense_weight"], "observations": row["observations"], "last_return": row["last_return"], "max_drawdown": row["max_drawdown"], "last_mark_at": row["last_mark_at"]}

    def save_learning_state(self, state: dict[str, float | int | str | None]) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO learning_state VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
            (state["attack_weight"], state["defense_weight"], state["observations"], state["last_return"], state["max_drawdown"], _iso(datetime.now(timezone.utc)), state.get("last_mark_at")),
        )
        self.connection.commit()

    def counts(self) -> dict[str, int]:
        tables = ("events", "market_bars", "fundamentals", "insights", "paper_orders", "paper_positions")
        return {table: int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}


def _iso(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
