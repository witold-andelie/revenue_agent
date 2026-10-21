from datetime import datetime, timedelta, timezone

import pytest

from revenue_agent import PaperBroker, SQLiteStore, update_strategy_from_equity


def test_learning_moves_to_defense_after_loss(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    try:
        broker = PaperBroker(store, initial_cash=1000)
        now = datetime(2026, 8, 1, tzinfo=timezone.utc)
        broker.mark_to_market({}, now)
        broker.mark_to_market({}, now + timedelta(days=1))
        # No open position means the equity is flat; create a deterministic loss through cash fees.
        broker.submit_market_order("AMD", "buy", 100, 100)
        broker.mark_to_market({"AMD": 90}, now + timedelta(days=2))
        update = update_strategy_from_equity(store)
        assert update.return_rate < 0
        assert update.defense_weight > update.attack_weight
    finally:
        store.close()


def test_repeated_learn_on_unchanged_history_is_a_noop(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    try:
        broker = PaperBroker(store, initial_cash=1000)
        now = datetime(2026, 8, 1, tzinfo=timezone.utc)
        broker.mark_to_market({}, now)
        broker.submit_market_order("AMD", "buy", 100, 100)
        broker.mark_to_market({"AMD": 90}, now + timedelta(days=1))
        first = update_strategy_from_equity(store)
        assert first.return_rate < 0
        # Learning again without any new mark must not compound the same shift.
        with pytest.raises(ValueError, match="没有新的 paper mark"):
            update_strategy_from_equity(store)
        # One new mark is enough for a fresh window anchored on the last consumed mark.
        broker.mark_to_market({"AMD": 110}, now + timedelta(days=2))
        second = update_strategy_from_equity(store)
        assert second.return_rate == pytest.approx(1009.9 / 989.9 - 1.0)
        assert store.learning_state()["observations"] == 2
    finally:
        store.close()
