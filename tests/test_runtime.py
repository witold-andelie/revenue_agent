from datetime import datetime, timezone

import pytest

from revenue_agent import AgentRuntime, AssetProfile, Event


class FakeLLM:
    def complete(self, messages):
        assert "untrusted evidence" in messages[0]["content"]
        return '{"insights":[{"id":"i1","title":"AMD thesis","thesis":"ecosystem may gain share","affected_entities":["AMD"],"horizon":"12m","confidence":0.7,"evidence_ids":["e1"],"direction":"long","invalid_if":"revenue growth reverses"}]}'


def test_runtime_requires_adoption_before_applying_insight() -> None:
    event = Event("e1", datetime.now(timezone.utc), "filing", "AMD expands", "growth support", ("AMD",), 0.9)
    runtime = AgentRuntime(FakeLLM(), (AssetProfile("AMD", "semiconductors"),))
    insight = runtime.research((event,))[0]
    with pytest.raises(PermissionError):
        runtime.apply_adopted(insight, (event,))
    runtime.adopt(insight, "human-1", "Reviewed filings")
    assert runtime.apply_adopted(insight, (event,))
