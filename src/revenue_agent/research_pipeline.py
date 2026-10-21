from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .domain import Evidence, Event, FundamentalSnapshot, MarketBar, Opportunity, Position, Score
from .engine import ResearchEngine
from .ledger import AuditLedger


@dataclass(frozen=True)
class AssetProfile:
    symbol: str
    sector: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class PaperSignal:
    symbol: str
    observed_at: datetime
    direction: str
    weight: float
    score: Score
    thesis: str


class ResearchPipeline:
    """Replayable event-to-paper-signal pipeline.

    This is intentionally a baseline. It prevents future data from entering
    the feature set and gives a reference point for a later learned scorer.
    """

    POSITIVE_TERMS = ("growth", "beat", "partnership", "expands", "support", "lower price", "demand")
    NEGATIVE_TERMS = ("decline", "miss", "lawsuit", "shortage", "recall", "shutdown", "higher cost", "bubble")

    def __init__(self, profiles: tuple[AssetProfile, ...], ledger: AuditLedger | None = None) -> None:
        self.profiles = profiles
        self.ledger = ledger
        self.engine = ResearchEngine()

    def run(
        self,
        events: tuple[Event, ...],
        bars: tuple[MarketBar, ...] = (),
        fundamentals: tuple[FundamentalSnapshot, ...] = (),
        *,
        min_abs_score: float = 0.05,
        max_position: float = 0.2,
    ) -> tuple[PaperSignal, ...]:
        if not 0 < max_position <= 1:
            raise ValueError("max_position must be between 0 and 1")
        for event in events:
            self.engine.ingest(event)
            if self.ledger:
                self.ledger.append("event_ingested", event)

        fundamentals_by_symbol = {item.symbol: item for item in fundamentals}
        signals: list[PaperSignal] = []
        for event in events:
            for profile in self.profiles:
                if not self._mentions(event, profile):
                    continue
                evidence = self._evidence(event, profile)
                if evidence is None:
                    continue
                opportunity = Opportunity(
                    id=f"{event.id}:{profile.symbol}",
                    entity=profile.symbol,
                    title=event.title,
                    observed_at=event.observed_at,
                    evidence=(evidence,),
                    expected_edge=self._prior_return(profile.symbol, event.observed_at, bars) * 0.25
                    + self._fundamental_adjustment(fundamentals_by_symbol.get(profile.symbol)),
                    execution_cost=0.001,
                )
                _heuristic, score = self.engine.score(opportunity)
                if abs(score.expected_net_edge) < min_abs_score:
                    continue
                direction = "long" if score.expected_net_edge > 0 else "short"
                weight = min(max_position, abs(score.expected_net_edge) * max_position)
                signal = PaperSignal(
                    symbol=profile.symbol,
                    observed_at=event.observed_at,
                    direction=direction,
                    weight=weight,
                    score=score,
                    thesis=f"{profile.symbol}: {event.title}; {evidence.rationale}",
                )
                signals.append(signal)
                if self.ledger:
                    self.ledger.append("paper_signal", signal)
        return tuple(signals)

    def _mentions(self, event: Event, profile: AssetProfile) -> bool:
        haystack = f"{event.title} {event.body}".casefold()
        names = (profile.symbol, *profile.aliases)
        return any(name.casefold() in haystack for name in names) or profile.symbol in event.entities

    def _evidence(self, event: Event, profile: AssetProfile) -> Evidence | None:
        text = f"{event.title} {event.body}".casefold()
        positive = sum(term in text for term in self.POSITIVE_TERMS)
        negative = sum(term in text for term in self.NEGATIVE_TERMS)
        if positive == negative:
            return None
        direction = 1.0 if positive > negative else -1.0
        strength = min(1.0, 0.25 + abs(positive - negative) * 0.15)
        rationale = f"rule impact for {profile.symbol}: {positive} positive terms, {negative} negative terms"
        return Evidence(event.id, profile.symbol, strength, direction, event.source_reliability, rationale)

    @staticmethod
    def _prior_return(symbol: str, observed_at: datetime, bars: tuple[MarketBar, ...]) -> float:
        history = sorted((bar for bar in bars if bar.symbol == symbol and bar.observed_at <= observed_at), key=lambda bar: bar.observed_at)
        if len(history) < 2 or history[0].close == 0:
            return 0.0
        window = history[-min(5, len(history)):]
        return (window[-1].close / window[0].close) - 1.0

    @staticmethod
    def _fundamental_adjustment(snapshot: FundamentalSnapshot | None) -> float:
        if snapshot is None:
            return 0.0
        values = [value for value in (snapshot.revenue_growth, snapshot.profit_margins, snapshot.operating_margins) if value is not None]
        if not values:
            return 0.0
        return max(-0.1, min(0.1, sum(values) / len(values) * 0.1))
