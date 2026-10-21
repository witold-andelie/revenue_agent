from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from .domain import MarketBar


@dataclass(frozen=True)
class AssetCandidate:
    symbol: str
    role: str
    sector: str
    fundamental_support: float


@dataclass(frozen=True)
class LegState:
    symbol: str
    role: str
    current_return: float
    crowding: float
    valuation_percentile: float
    fundamental_support: float


@dataclass(frozen=True)
class BarbellConfig:
    attack_weight: float = 0.5
    defense_weight: float = 0.5
    switched_attack_weight: float = 0.25
    switched_defense_weight: float = 0.75
    high_return_threshold: float = 0.12
    crowding_threshold: float = 0.7
    low_valuation_threshold: float = 0.35
    min_fundamental_support: float = 0.5
    max_abs_correlation: float = 0.35
    max_turnover: float = 0.25
    transaction_cost_bps: float = 15.0


@dataclass(frozen=True)
class PairCandidate:
    attack_symbol: str
    defense_symbol: str
    correlation: float
    rationale: str


@dataclass(frozen=True)
class RebalanceDecision:
    target_weights: dict[str, float]
    trades: dict[str, float]
    estimated_cost: float
    reasons: tuple[str, ...]
    risk_flags: tuple[str, ...]


def find_weakly_correlated_pairs(
    candidates: tuple[AssetCandidate, ...],
    bars: tuple[MarketBar, ...],
    *,
    max_abs_correlation: float = 0.35,
    min_observations: int = 20,
) -> tuple[PairCandidate, ...]:
    attacks = [candidate for candidate in candidates if candidate.role == "attack"]
    defenses = [candidate for candidate in candidates if candidate.role == "defense"]
    result = []
    for attack, defense in combinations(attacks + defenses, 2):
        if {attack.role, defense.role} != {"attack", "defense"}:
            continue
        correlation, observations = _correlation(attack.symbol, defense.symbol, bars)
        # Strong negative correlation is useful for a hedge; only strong
        # positive correlation defeats the seesaw objective.
        if observations >= min_observations and correlation <= max_abs_correlation:
            attack_symbol = attack.symbol if attack.role == "attack" else defense.symbol
            defense_symbol = defense.symbol if defense.role == "defense" else attack.symbol
            result.append(PairCandidate(attack_symbol, defense_symbol, correlation, "weakly correlated cross-role pair"))
    return tuple(sorted(result, key=lambda pair: abs(pair.correlation)))


def rebalance_barbell(
    attack: LegState,
    defense: LegState,
    current_weights: dict[str, float],
    config: BarbellConfig = BarbellConfig(),
) -> RebalanceDecision:
    if attack.role != "attack" or defense.role != "defense":
        raise ValueError("roles must be attack and defense")
    if attack.symbol == defense.symbol:
        raise ValueError("attack and defense symbols must differ")
    if not 0 <= attack.crowding <= 1 or not 0 <= defense.crowding <= 1:
        raise ValueError("crowding must be between 0 and 1")
    if not 0 <= attack.valuation_percentile <= 1 or not 0 <= defense.valuation_percentile <= 1:
        raise ValueError("valuation_percentile must be between 0 and 1")

    target_attack = config.attack_weight
    target_defense = config.defense_weight
    reasons = ["maintain barbell allocation"]
    risk_flags: list[str] = []
    attack_high = attack.current_return >= config.high_return_threshold or attack.crowding >= config.crowding_threshold
    defense_ready = (
        defense.valuation_percentile <= config.low_valuation_threshold
        and defense.fundamental_support >= config.min_fundamental_support
    )
    if attack_high and defense_ready:
        target_attack = config.switched_attack_weight
        target_defense = config.switched_defense_weight
        reasons = ["attack leg is high or crowded", "defense leg is low with fundamental support"]

    if attack.fundamental_support < config.min_fundamental_support:
        risk_flags.append("attack fundamental support is weak")
    if defense.fundamental_support < config.min_fundamental_support:
        risk_flags.append("defense fundamental support is weak; possible value trap")
    if attack.current_return <= -0.15 and defense.current_return <= -0.15:
        risk_flags.append("both legs are falling; hedge may be failing in a systemic selloff")

    desired = {attack.symbol: target_attack, defense.symbol: target_defense}
    all_symbols = set(current_weights) | set(desired)
    raw_trades = {symbol: desired.get(symbol, 0.0) - current_weights.get(symbol, 0.0) for symbol in all_symbols}
    turnover = sum(abs(value) for value in raw_trades.values())
    if turnover > config.max_turnover and turnover > 0:
        scale = config.max_turnover / turnover
        trades = {symbol: value * scale for symbol, value in raw_trades.items()}
        reasons.append("rebalance clipped by maximum turnover")
    else:
        trades = raw_trades
    estimated_cost = sum(abs(value) for value in trades.values()) * config.transaction_cost_bps / 10_000
    target_weights = {symbol: current_weights.get(symbol, 0.0) + trades[symbol] for symbol in all_symbols}
    return RebalanceDecision(target_weights, trades, estimated_cost, tuple(reasons), tuple(risk_flags))


def register_barbell_tools(registry, candidates: tuple[AssetCandidate, ...], bars: tuple[MarketBar, ...]) -> None:
    """Expose research-only barbell tools to the bounded LLM planner."""
    registry.register(
        "find_barbell_pairs",
        lambda _args: [
            {
                "attack_symbol": pair.attack_symbol,
                "defense_symbol": pair.defense_symbol,
                "correlation": pair.correlation,
                "rationale": pair.rationale,
            }
            for pair in find_weakly_correlated_pairs(candidates, bars)
        ],
        description="Find attack/defense pairs that avoid strong positive correlation",
        parameters={"type": "object", "properties": {}},
    )


def _correlation(left: str, right: str, bars: tuple[MarketBar, ...]) -> tuple[float, int]:
    closes: dict[str, dict[object, float]] = {left: {}, right: {}}
    for bar in bars:
        if bar.symbol in closes:
            closes[bar.symbol][bar.observed_at] = bar.close
    timestamps = sorted(set(closes[left]) & set(closes[right]))
    if len(timestamps) < 3:
        return 0.0, 0
    left_returns = [closes[left][b] / closes[left][a] - 1 for a, b in zip(timestamps, timestamps[1:])]
    right_returns = [closes[right][b] / closes[right][a] - 1 for a, b in zip(timestamps, timestamps[1:])]
    return _pearson(left_returns, right_returns), len(left_returns)


def _pearson(left: list[float], right: list[float]) -> float:
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_norm = sum((value - left_mean) ** 2 for value in left) ** 0.5
    right_norm = sum((value - right_mean) ** 2 for value in right) ** 0.5
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0
