from datetime import datetime, timedelta, timezone

from revenue_agent import AssetProfile, Event, FundamentalSnapshot, MarketBar, ResearchPipeline


def test_pipeline_uses_only_pre_event_prices_and_emits_paper_signal() -> None:
    event_time = datetime(2026, 8, 5, tzinfo=timezone.utc)
    event = Event("e1", event_time, "filing", "AMD expands AI ecosystem", "Demand growth and lower price support", ("AMD",), 0.9)
    bars = (
        MarketBar("AMD", event_time - timedelta(days=2), 10, 10, 9, 10, 100),
        MarketBar("AMD", event_time - timedelta(days=1), 10, 11, 10, 11, 100),
        MarketBar("AMD", event_time + timedelta(days=1), 11, 20, 11, 20, 100),
    )
    fundamentals = (FundamentalSnapshot("AMD", event_time, 100, 0.2, 0.1, 0.15, 30),)
    pipeline = ResearchPipeline((AssetProfile("AMD", "semiconductors", ("Advanced Micro Devices",)),))
    signals = pipeline.run(event and (event,), bars, fundamentals)
    assert len(signals) == 1
    assert signals[0].direction == "long"
    assert signals[0].score.attention["e1"] == 1.0
    without_future = ResearchPipeline((AssetProfile("AMD", "semiconductors", ("Advanced Micro Devices",)),)).run(
        (event,), bars[:2], fundamentals
    )
    assert signals[0].score.expected_net_edge == without_future[0].score.expected_net_edge


def test_pipeline_ignores_ambiguous_event() -> None:
    now = datetime.now(timezone.utc)
    event = Event("e1", now, "social", "AMD update", "AMD mentioned without a directional claim", ("AMD",), 0.3)
    signals = ResearchPipeline((AssetProfile("AMD", "semiconductors"),)).run((event,))
    assert signals == ()
