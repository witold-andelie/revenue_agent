from revenue_agent import Scenario, build_hedged_book
from revenue_agent.scenarios import default_scenarios


def test_scenario_produces_long_and_short_paper_positions() -> None:
    scenario = Scenario("ai_drawdown", 0.4, {"ai_infrastructure": -0.9, "software": 0.4}, "stress")
    book = build_hedged_book(scenario, {"ai_infrastructure": ("NVDA", "AMD"), "software": ("MSFT",)})
    by_entity = {position.entity: position for position in book}
    assert by_entity["NVDA"].direction == "short"
    assert by_entity["MSFT"].direction == "long"
    assert sum(position.weight for position in book) <= 1.0


def test_default_el_nino_regime_is_separate_from_extreme_tail() -> None:
    scenarios = {scenario.name: scenario for scenario in default_scenarios()}
    assert scenarios["el_nino_active"].probability == 0.97
    assert scenarios["el_nino_very_strong_tail"].probability == 0.81
