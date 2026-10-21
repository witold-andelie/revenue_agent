from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .storage import SQLiteStore


@dataclass(frozen=True)
class LearningUpdate:
    attack_weight: float
    defense_weight: float
    return_rate: float
    max_drawdown: float
    reason: str


def update_strategy_from_equity(store: SQLiteStore) -> LearningUpdate:
    """Learn from the equity marks observed since the previous update.

    Each call consumes only the new window, so repeated calls on an unchanged
    history are a no-op instead of compounding the same shift forever.
    """
    history = store.equity_history()
    previous = store.learning_state()
    window = _window_since(history, previous.get("last_mark_at") if previous else None)
    if len(window) < 2:
        raise ValueError("没有新的 paper mark 可以学习：自上次学习后至少需要一次新 mark")
    values = [value for _, value in window]
    start = values[0]
    return_rate = values[-1] / start - 1.0 if start else 0.0
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, 1.0 - value / peak if peak else 0.0)
    prior_observations = int((previous or {}).get("observations", 0))
    attack = float((previous or {"attack_weight": 0.5})["attack_weight"])
    if return_rate < 0 or max_drawdown >= 0.05:
        attack -= 0.05
        reason = "收益为负或回撤达到 5%，增加防御腿"
    elif return_rate > 0 and max_drawdown < 0.02:
        attack += 0.025
        reason = "收益为正且回撤低于 2%，小幅增加进攻腿"
    else:
        reason = "信号不足，保持当前权重"
    attack = min(0.75, max(0.25, attack))
    update = LearningUpdate(attack, 1.0 - attack, return_rate, max_drawdown, reason)
    store.save_learning_state({
        "attack_weight": update.attack_weight,
        "defense_weight": update.defense_weight,
        "observations": prior_observations + 1,
        "last_return": update.return_rate,
        "max_drawdown": update.max_drawdown,
        "last_mark_at": _iso_utc(window[-1][0]),
    })
    return update


def _window_since(history: tuple[tuple[datetime, float], ...], last_mark_at: str | None) -> list[tuple[datetime, float]]:
    """Marks after the last consumed point, anchored on that consumed point so
    the first new mark already yields a measurable return window."""
    if not last_mark_at:
        return list(history)
    cutoff = _parse_utc(last_mark_at)
    after = [entry for entry in history if entry[0] > cutoff]
    if not after:
        return []
    consumed = [entry for entry in history if entry[0] <= cutoff]
    return ([consumed[-1]] + after) if consumed else after


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso_utc(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
