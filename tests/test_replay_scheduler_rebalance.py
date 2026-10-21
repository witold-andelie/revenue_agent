from datetime import datetime, timedelta, timezone

import pytest

from revenue_agent import AssetProfile, BarbellConfig, Event, FundamentalSnapshot, LegState, MarketBar, ReplayRunner, ScheduleConfig, AgentScheduler, plan_barbell_orders
from revenue_agent.rebalancer import execute_barbell_orders
from revenue_agent.paper_broker import PaperBroker
from revenue_agent.storage import SQLiteStore


def test_replay_excludes_future_bars_and_fundamentals() -> None:
    event_time = datetime(2026, 8, 1, tzinfo=timezone.utc)
    event = Event("e1", event_time, "news", "AMD demand expands", "AMD demand growth", ("AMD",), 0.9)
    bars = (
        MarketBar("AMD", event_time - timedelta(days=1), 10, 11, 9, 10, 100),
        MarketBar("AMD", event_time + timedelta(days=1), 10, 30, 10, 30, 100),
    )
    future_fundamental = FundamentalSnapshot("AMD", event_time + timedelta(days=1), 100, -0.9, -0.9, -0.9, 90)
    runner = ReplayRunner((AssetProfile("AMD", "semiconductors"),))
    result = runner.run((event,), bars, (future_fundamental,))
    without_future = runner.run((event,), bars[:1], ())
    assert len(result.signals) == 1
    assert result.signals[0].score.expected_net_edge == without_future.signals[0].score.expected_net_edge


def test_scheduler_can_run_once_without_starting_thread() -> None:
    calls: list[int] = []
    scheduler = AgentScheduler(lambda: calls.append(1), ScheduleConfig(1))
    scheduler.run_once()
    assert calls == [1]


def test_scheduler_survives_callback_exceptions() -> None:
    attempts: list[int] = []

    def flaky() -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient cycle failure")

    scheduler = AgentScheduler(flaky, ScheduleConfig(interval_seconds=0.15))
    scheduler.start()
    try:
        while len(attempts) < 3:
            if not scheduler._thread.is_alive():
                raise AssertionError("scheduler thread died on callback exception")
            scheduler._thread.join(timeout=2)
    finally:
        scheduler.stop()
    assert len(attempts) >= 3


def test_barbell_orders_are_weight_deltas() -> None:
    decision, orders = plan_barbell_orders(
        LegState("AMD", "attack", 0.0, 0.0, 0.5, 0.8),
        LegState("TLT", "defense", 0.0, 0.0, 0.2, 0.8),
        {},
        {"AMD": 100.0, "TLT": 100.0},
        100_000.0,
        BarbellConfig(max_turnover=1.0),
    )
    assert decision.target_weights == {"AMD": 0.5, "TLT": 0.5}
    assert {order.symbol for order in orders} == {"AMD", "TLT"}
    # Buys carry a small fee reserve so planned rotations stay fundable.
    total = sum(order.notional for order in orders)
    assert 99_000.0 < total <= 100_000.0


def test_rebalance_sells_execute_before_buys(tmp_path) -> None:
    """A fully invested account must be able to rotate without tripping the
    leverage cap: sells free cash first."""
    store = SQLiteStore(tmp_path / "agent.db")
    try:
        broker = PaperBroker(store, initial_cash=100_000)
        broker.submit_market_order("AMD", "buy", 49_000, 100)  # 490 shares
        broker.submit_market_order("TLT", "buy", 49_000, 100)  # ~fully invested, fees leave cash
        prices = {"AMD": 100.0, "TLT": 100.0}
        snapshot = broker.snapshot(prices)
        _decision, orders = plan_barbell_orders(
            # High/crowded attack + cheap well-supported defense switches the
            # barbell to 25/75, forcing a sell on AMD and a buy on TLT.
            LegState("AMD", "attack", 0.15, 0.8, 0.5, 0.8),
            LegState("TLT", "defense", 0.0, 0.0, 0.2, 0.8),
            snapshot["weights"],
            prices,
            snapshot["equity"],
            BarbellConfig(max_turnover=1.0),
        )
        sides = [order.side for order in orders]
        assert "sell" in sides and "buy" in sides
        assert sides.index("sell") < sides.index("buy")
        execute_barbell_orders(broker, tuple(orders), "s1")
    finally:
        store.close()
