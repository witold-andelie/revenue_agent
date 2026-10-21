from __future__ import annotations

from datetime import datetime, timezone

from .governance import CapabilityGate, RuntimeCapabilities
from .storage import SQLiteStore

_EPSILON = 1e-9


class PaperBroker:
    """Restartable paper account. No network or live broker access.

    Risk gates mirror the Rust gateway: uncovered selling and shorting are
    opt-in, and gross exposure is capped against configured max leverage.
    Position marks use the traded price for the ordered symbol and stored
    average prices as a conservative proxy for every other holding.
    """

    def __init__(
        self,
        store: SQLiteStore,
        initial_cash: float = 100_000.0,
        fee_bps: float = 10.0,
        capabilities: RuntimeCapabilities | None = None,
    ) -> None:
        if initial_cash <= 0 or fee_bps < 0:
            raise ValueError("invalid paper account configuration")
        self.store = store
        self.fee_bps = fee_bps
        self.capabilities = capabilities or RuntimeCapabilities()
        self.gate = CapabilityGate(self.capabilities)
        now = _now()
        self.store.connection.execute("INSERT OR IGNORE INTO paper_account(id, cash, updated_at) VALUES (1, ?, ?)", (initial_cash, now))
        self.store.connection.commit()

    def submit_market_order(self, symbol: str, side: str, notional: float, price: float, strategy_id: str | None = None) -> int:
        if not symbol or side not in {"buy", "sell", "short", "cover"}:
            raise ValueError("invalid paper order")
        if notional <= 0 or price <= 0:
            raise ValueError("notional and price must be positive")
        if side == "short":
            self.gate.require_uncovered_short()
        quantity = notional / price
        fee = notional * self.fee_bps / 10_000
        signed_quantity = quantity if side in {"buy", "cover"} else -quantity
        cash_delta = -(notional + fee) if side in {"buy", "cover"} else notional - fee
        now = _now()
        connection = self.store.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            cash = float(connection.execute("SELECT cash FROM paper_account WHERE id = 1").fetchone()[0])
            if cash + cash_delta < 0 and side in {"buy", "cover"}:
                raise ValueError("insufficient paper cash")
            row = connection.execute("SELECT quantity, average_price FROM paper_positions WHERE symbol = ?", (symbol,)).fetchone()
            old_quantity = float(row[0]) if row else 0.0
            old_average = float(row[1]) if row else 0.0
            if side == "sell" and not self.capabilities.enable_uncovered_short and quantity > old_quantity + _EPSILON:
                raise ValueError("uncovered sell exceeds the held position")
            new_quantity = old_quantity + signed_quantity
            new_average = _merged_average(old_quantity, old_average, signed_quantity, price)
            self._require_leverage_within_cap(connection, symbol, new_quantity, price, cash + cash_delta)
            connection.execute("UPDATE paper_account SET cash = ?, updated_at = ? WHERE id = 1", (cash + cash_delta, now))
            if abs(new_quantity) < _EPSILON:
                connection.execute("DELETE FROM paper_positions WHERE symbol = ?", (symbol,))
            else:
                connection.execute("INSERT OR REPLACE INTO paper_positions VALUES (?, ?, ?, ?)", (symbol, new_quantity, new_average, now))
            cursor = connection.execute("INSERT INTO paper_orders(symbol, side, quantity, price, notional, fee, status, created_at, strategy_id) VALUES (?, ?, ?, ?, ?, ?, 'filled', ?, ?)", (symbol, side, quantity, price, notional, fee, now, strategy_id))
            connection.commit()
            return int(cursor.lastrowid)
        except Exception:
            connection.rollback()
            raise

    def _require_leverage_within_cap(self, connection, traded_symbol: str, traded_quantity: float, traded_price: float, projected_cash: float) -> None:
        projected_value = traded_quantity * traded_price
        gross = abs(projected_value)
        for row in connection.execute("SELECT symbol, quantity, average_price FROM paper_positions").fetchall():
            if row[0] == traded_symbol:
                continue
            reference = float(row[2])
            value = float(row[1]) * reference
            projected_value += value
            gross += abs(value)
        equity = projected_cash + projected_value
        if equity <= _EPSILON:
            raise PermissionError("paper account equity is non-positive; only risk-reducing sells remain allowed")
        self.gate.require_leverage(gross / equity)

    def mark_to_market(self, prices: dict[str, float], observed_at: datetime | None = None) -> dict[str, float]:
        connection = self.store.connection
        cash = float(connection.execute("SELECT cash FROM paper_account WHERE id = 1").fetchone()[0])
        market_value = 0.0
        for row in connection.execute("SELECT symbol, quantity FROM paper_positions").fetchall():
            if row[0] not in prices or prices[row[0]] <= 0:
                raise ValueError(f"missing valid mark for {row[0]}")
            market_value += float(row[1]) * prices[row[0]]
        equity = cash + market_value
        timestamp = _now() if observed_at is None else _now(observed_at)
        connection.execute("INSERT OR REPLACE INTO paper_equity VALUES (?, ?, ?, ?)", (timestamp, cash, market_value, equity))
        connection.commit()
        return {"cash": cash, "market_value": market_value, "equity": equity}

    def positions(self) -> dict[str, float]:
        return {row[0]: float(row[1]) for row in self.store.connection.execute("SELECT symbol, quantity FROM paper_positions")}

    def snapshot(self, prices: dict[str, float]) -> dict[str, float]:
        marked = self.mark_to_market(prices)
        equity = marked["equity"]
        weights = {symbol: quantity * prices[symbol] / equity for symbol, quantity in self.positions().items() if symbol in prices and equity > 0}
        return {**marked, "weights": weights}


def _merged_average(old_quantity: float, old_average: float, signed_quantity: float, price: float) -> float:
    """Opening or flipping through zero reprices at the trade; a partial
    reduction keeps the original cost basis; adding scales the average."""
    if abs(old_quantity) < _EPSILON:
        return price
    reducing = old_quantity * signed_quantity < 0
    if reducing and abs(signed_quantity) > abs(old_quantity):
        return price
    if reducing:
        return old_average
    return (abs(old_quantity) * old_average + abs(signed_quantity) * price) / (abs(old_quantity) + abs(signed_quantity))


def _now(value: datetime | None = None) -> str:
    value = value or datetime.now(timezone.utc)
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
