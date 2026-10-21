from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .domain import FundamentalSnapshot, MarketBar


class YahooFinanceSource:
    """Read market history and fundamentals through the local yfinance install."""

    def __init__(self, symbols: tuple[str, ...], *, period: str = "1mo", interval: str = "1d", yf_module: Any | None = None) -> None:
        if not symbols:
            raise ValueError("at least one symbol is required")
        self.symbols = symbols
        self.period = period
        self.interval = interval
        self.yf_module = yf_module

    def fetch(self) -> tuple[MarketBar, ...]:
        yf = self.yf_module or _load_yfinance()
        bars: list[MarketBar] = []
        for symbol in self.symbols:
            history = yf.Ticker(symbol).history(period=self.period, interval=self.interval, auto_adjust=False)
            for timestamp, row in history.iterrows():
                bars.append(MarketBar(
                    symbol=symbol,
                    observed_at=_timestamp(timestamp),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row["Volume"]),
                ))
        return tuple(bars)

    def fetch_fundamentals(self) -> tuple[FundamentalSnapshot, ...]:
        yf = self.yf_module or _load_yfinance()
        observed_at = datetime.now(timezone.utc)
        snapshots = []
        for symbol in self.symbols:
            info = yf.Ticker(symbol).info
            snapshots.append(FundamentalSnapshot(
                symbol=symbol,
                observed_at=observed_at,
                market_cap=_number(info.get("marketCap")),
                revenue_growth=_number(info.get("revenueGrowth")),
                profit_margins=_number(info.get("profitMargins")),
                operating_margins=_number(info.get("operatingMargins")),
                trailing_pe=_number(info.get("trailingPE")),
            ))
        return tuple(snapshots)


def _load_yfinance() -> Any:
    import yfinance

    return yfinance


def _timestamp(value: Any) -> datetime:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
