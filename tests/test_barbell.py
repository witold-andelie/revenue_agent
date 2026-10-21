from datetime import datetime, timedelta, timezone

from revenue_agent import AssetCandidate, BarbellConfig, LegState, MarketBar, ToolCall, ToolRegistry
from revenue_agent.barbell import find_weakly_correlated_pairs, rebalance_barbell, register_barbell_tools


def _bars() -> tuple[MarketBar, ...]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    attack_prices = [100, 101, 99, 102, 98, 103, 97, 104, 96, 105, 95, 106, 94, 107, 93, 108, 92, 109, 91, 110, 90]
    defense_prices = [100, 99.8, 100, 99.8, 100, 99.8, 100, 99.8, 100, 99.8, 100, 99.8, 100, 99.8, 100, 99.8, 100, 99.8, 100, 99.8, 100]
    return tuple(
        MarketBar(symbol, start + timedelta(days=i), price, price, price, price, 100)
        for symbol, prices in (("ATTACK", attack_prices), ("DEFENSE", defense_prices))
        for i, price in enumerate(prices)
    )


def test_pair_selector_rejects_strongly_correlated_assets_and_accepts_weak_pair() -> None:
    pairs = find_weakly_correlated_pairs(
        (AssetCandidate("ATTACK", "attack", "technology", 0.8), AssetCandidate("DEFENSE", "defense", "dividend", 0.8)),
        _bars(), min_observations=20,
    )
    assert len(pairs) == 1
    assert pairs[0].correlation < 0


def test_rebalance_moves_profit_from_crowded_attack_to_supported_defense() -> None:
    decision = rebalance_barbell(
        LegState("ATTACK", "attack", 0.2, 0.8, 0.8, 0.8),
        LegState("DEFENSE", "defense", 0.01, 0.1, 0.2, 0.8),
        {"ATTACK": 0.5, "DEFENSE": 0.5},
        BarbellConfig(max_turnover=1.0),
    )
    assert decision.target_weights == {"ATTACK": 0.25, "DEFENSE": 0.75}
    assert decision.estimated_cost > 0


def test_rebalance_flags_systemic_selloff() -> None:
    decision = rebalance_barbell(
        LegState("ATTACK", "attack", -0.2, 0.2, 0.5, 0.8),
        LegState("DEFENSE", "defense", -0.2, 0.2, 0.5, 0.8),
        {"ATTACK": 0.5, "DEFENSE": 0.5},
    )
    assert "hedge may be failing" in decision.risk_flags[0]


def test_barbell_pair_finder_is_available_as_a_planner_tool() -> None:
    registry = ToolRegistry()
    register_barbell_tools(
        registry,
        (AssetCandidate("ATTACK", "attack", "technology", 0.8), AssetCandidate("DEFENSE", "defense", "dividend", 0.8)),
        _bars(),
    )
    result = registry.execute(ToolCall("find_barbell_pairs", {}))
    assert result.ok is True
    assert result.value[0]["attack_symbol"] == "ATTACK"
