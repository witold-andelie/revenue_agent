from __future__ import annotations

from .domain import Evidence, Event, Opportunity, Score
from .scoring import AttentionScorer, HeuristicScorer
from .sources import EventSource


class ResearchEngine:
    def __init__(self) -> None:
        self.events: dict[str, Event] = {}
        self.evidence: list[Evidence] = []

    def ingest(self, event: Event) -> None:
        if not 0 <= event.source_reliability <= 1:
            raise ValueError("source_reliability must be between 0 and 1")
        self.events[event.id] = event

    def refresh(self, source: EventSource) -> int:
        count = 0
        for event in source.fetch():
            self.ingest(event)
            count += 1
        return count

    def make_opportunity(self, opportunity: Opportunity) -> Opportunity:
        event_ids = {event.id for event in self.events.values()}
        if any(e.event_id not in event_ids for e in opportunity.evidence):
            raise ValueError("opportunity references unknown evidence event")
        return opportunity

    def score(self, opportunity: Opportunity) -> tuple[Score, Score]:
        opportunity = self.make_opportunity(opportunity)
        source_weights = {event.id: event.source_reliability for event in self.events.values()}
        return HeuristicScorer().score(opportunity), AttentionScorer(source_weights).score(opportunity)
