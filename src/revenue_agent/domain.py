from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


Direction = Literal["long", "short"]


@dataclass(frozen=True)
class Event:
    id: str
    observed_at: datetime
    source: str
    title: str
    body: str
    entities: tuple[str, ...]
    source_reliability: float = 0.5
    url: str | None = None


@dataclass(frozen=True)
class Evidence:
    event_id: str
    entity: str
    relevance: float
    direction: float
    confidence: float
    rationale: str


@dataclass(frozen=True)
class Opportunity:
    id: str
    entity: str
    title: str
    observed_at: datetime
    evidence: tuple[Evidence, ...] = ()
    expected_edge: float = 0.0
    execution_cost: float = 0.0


@dataclass(frozen=True)
class Scenario:
    name: str
    probability: float
    impacts: dict[str, float]
    rationale: str


@dataclass(frozen=True)
class Position:
    entity: str
    direction: Direction
    weight: float
    thesis: str
    hedge_for: tuple[str, ...] = ()


@dataclass(frozen=True)
class Score:
    opportunity_id: str
    raw_score: float
    confidence: float
    expected_net_edge: float
    attention: dict[str, float] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarketBar:
    symbol: str
    observed_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str = "yahoo_finance"


@dataclass(frozen=True)
class FundamentalSnapshot:
    symbol: str
    observed_at: datetime
    market_cap: float | None
    revenue_growth: float | None
    profit_margins: float | None
    operating_margins: float | None
    trailing_pe: float | None
    source: str = "yahoo_finance"


@dataclass(frozen=True)
class Insight:
    id: str
    title: str
    thesis: str
    affected_entities: tuple[str, ...]
    horizon: str
    confidence: float
    evidence_ids: tuple[str, ...] = ()
    direction: str = "neutral"
    invalid_if: str | None = None


@dataclass(frozen=True)
class StrategyActivation:
    insight_id: str
    human_id: str
    adopted_at: datetime
    rationale: str
