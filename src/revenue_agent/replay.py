from __future__ import annotations

from dataclasses import dataclass

from .domain import Event, FundamentalSnapshot, MarketBar
from .research_pipeline import AssetProfile, PaperSignal, ResearchPipeline


@dataclass(frozen=True)
class ReplayResult:
    signals: tuple[PaperSignal, ...]
    events_seen: int


class ReplayRunner:
    """Run events in timestamp order using only data available at each event."""

    def __init__(self, profiles: tuple[AssetProfile, ...]) -> None:
        self.profiles = profiles

    def run(
        self,
        events: tuple[Event, ...],
        bars: tuple[MarketBar, ...] = (),
        fundamentals: tuple[FundamentalSnapshot, ...] = (),
    ) -> ReplayResult:
        pipeline = ResearchPipeline(self.profiles)
        signals: list[PaperSignal] = []
        for event in sorted(events, key=lambda item: item.observed_at):
            available_bars = tuple(bar for bar in bars if bar.observed_at <= event.observed_at)
            available_fundamentals = tuple(
                snapshot for snapshot in fundamentals
                if snapshot.observed_at <= event.observed_at
            )
            signals.extend(pipeline.run((event,), available_bars, available_fundamentals))
        return ReplayResult(tuple(signals), len(events))
