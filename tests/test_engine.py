from datetime import datetime, timezone

import pytest

from revenue_agent import Evidence, Event, Opportunity, ResearchEngine


def test_event_must_be_ingested_before_scoring() -> None:
    engine = ResearchEngine()
    opportunity = Opportunity(
        "o1", "AMD", "AI accelerator ecosystem", datetime.now(timezone.utc),
        (Evidence("e1", "AMD", 1.0, 1.0, 0.8, "verified filing"),),
    )
    with pytest.raises(ValueError, match="unknown evidence"):
        engine.score(opportunity)


def test_attention_score_prioritizes_reliable_evidence() -> None:
    engine = ResearchEngine()
    now = datetime.now(timezone.utc)
    engine.ingest(Event("e1", now, "filing", "Filing", "", ("AMD",), 0.95))
    engine.ingest(Event("e2", now, "social", "Rumor", "", ("AMD",), 0.2))
    opportunity = Opportunity(
        "o1", "AMD", "AI accelerator ecosystem", now,
        (Evidence("e1", "AMD", 0.8, 1.0, 0.9, "filing supports demand"),
         Evidence("e2", "AMD", 1.0, 1.0, 0.9, "social discussion")),
    )
    _heuristic, attention = engine.score(opportunity)
    assert attention.attention["e1"] > attention.attention["e2"]


def test_untrusted_event_cannot_claim_high_reliability() -> None:
    engine = ResearchEngine()
    with pytest.raises(ValueError):
        engine.ingest(Event("e1", datetime.now(timezone.utc), "social", "", "", (), 1.1))
