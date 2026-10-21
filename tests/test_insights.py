import pytest

from datetime import datetime, timezone

from revenue_agent import Event
from revenue_agent.insights import InsightGenerator, _validate


def test_direction_aliases_are_normalized() -> None:
    insight = _validate(
        {
            "id": "i1",
            "title": "Demand",
            "thesis": "Demand is improving",
            "affected_entities": ["AMD"],
            "horizon": "long_term",
            "confidence": 0.7,
            "evidence_ids": ["e1"],
            "direction": "up",
        },
        {"e1"},
    )
    assert insight.direction == "long"


def test_insight_id_is_derived_from_content_not_llm_choice() -> None:
    item = {
        "id": "attacker-chosen-id",
        "title": "Demand",
        "thesis": "Demand is improving",
        "affected_entities": ["AMD"],
        "horizon": "long_term",
        "confidence": 0.7,
        "evidence_ids": ["e1"],
        "direction": "long",
    }
    first = _validate(item, {"e1"})
    second = _validate(item, {"e1"})
    assert first.id == second.id
    assert first.id != "attacker-chosen-id" and len(first.id) == 24
    changed = dict(item, direction="short")
    assert _validate(changed, {"e1"}).id != first.id


def test_more_than_three_insights_are_rejected() -> None:
    from revenue_agent.insights import InsightGenerator

    class LLM:
        def complete(self, messages):
            item = '{"id":"x","title":"t","thesis":"th","affected_entities":[],"horizon":"","confidence":0.5,"evidence_ids":["e1"],"direction":"neutral"}'
            return '{"insights":[%s]}' % ",".join([item] * 4)

    event = Event("e1", datetime(2026, 8, 1, tzinfo=timezone.utc), "s", "t", "b", (), 0.9)
    with pytest.raises(ValueError, match="at most 3"):
        InsightGenerator(LLM()).generate((event,))


def test_moltbook_focus_terms_are_prioritized() -> None:
    from revenue_agent.moltbook_source import MoltbookEventSource

    class Client:
        def fetch_posts(self, *, limit):
            return {"posts": [
                {"id": "other", "title": "Other", "content": "", "created_at": "2026-08-08T00:00:00Z"},
                {"id": "focus", "title": "aleabitoreddit research", "content": "", "created_at": "2026-08-08T00:00:00Z"},
            ]}

    events = MoltbookEventSource(Client(), focus_terms=("aleabitoreddit",)).fetch()
    assert events[0].id != events[1].id
    assert "aleabitoreddit" in events[0].title.lower()


def test_moltbook_posts_without_valid_timestamp_are_dropped() -> None:
    from revenue_agent.moltbook_source import MoltbookEventSource

    class Client:
        def fetch_posts(self, *, limit):
            return {"posts": [
                {"id": "no-time", "title": "Missing", "content": ""},
                {"id": "bad-time", "title": "Broken", "content": "", "created_at": "not-a-date"},
                {"id": "good", "title": "Valid", "content": "", "created_at": "2026-08-08T00:00:00Z"},
            ]}

    events = MoltbookEventSource(Client()).fetch()
    assert [event.title for event in events] == ["Valid"]
