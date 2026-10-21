from datetime import datetime, timezone

from revenue_agent import Event, ResearchEngine, StaticEventSource


def test_refresh_ingests_events_from_source() -> None:
    event = Event("e1", datetime.now(timezone.utc), "rss", "AMD", "", ("AMD",), 0.7)
    engine = ResearchEngine()
    assert engine.refresh(StaticEventSource([event])) == 1
    assert engine.events["e1"] == event
