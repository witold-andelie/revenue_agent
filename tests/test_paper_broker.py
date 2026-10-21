import pytest

from revenue_agent import PaperBroker, RuntimeCapabilities, SQLiteStore


def test_paper_broker_persists_orders_positions_and_marks(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    broker = PaperBroker(store, initial_cash=1000, fee_bps=10)
    order_id = broker.submit_market_order("AMD", "buy", 400, 100, "i1")
    snapshot = broker.mark_to_market({"AMD": 110})
    assert order_id == 1
    assert snapshot["equity"] > 1000
    store.close()

    reopened = SQLiteStore(tmp_path / "agent.db")
    assert reopened.counts()["paper_orders"] == 1
    assert reopened.counts()["paper_positions"] == 1
    reopened.close()


def test_paper_broker_rejects_missing_marks_and_insufficient_cash(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    broker = PaperBroker(store, initial_cash=100)
    try:
        broker.submit_market_order("AMD", "buy", 200, 100)
    except ValueError as error:
        assert "cash" in str(error)
    else:
        raise AssertionError("oversized order was accepted")
    shorting = PaperBroker(
        store,
        initial_cash=100,
        capabilities=RuntimeCapabilities(enable_uncovered_short=True),
    )
    shorting.submit_market_order("AMD", "short", 50, 100)
    try:
        shorting.mark_to_market({})
    except ValueError as error:
        assert "mark" in str(error)
    else:
        raise AssertionError("missing mark was accepted")
    store.close()


def test_uncovered_sell_is_rejected_by_default(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    broker = PaperBroker(store, initial_cash=1000)
    broker.submit_market_order("AMD", "buy", 100, 10)
    with pytest.raises(ValueError, match="uncovered sell"):
        broker.submit_market_order("AMD", "sell", 500, 10)
    assert broker.positions() == {"AMD": pytest.approx(10.0)}
    store.close()


def test_short_is_disabled_by_default_and_margin_capped_when_enabled(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    broker = PaperBroker(store, initial_cash=1000)
    with pytest.raises(PermissionError, match="uncovered short"):
        broker.submit_market_order("AMD", "short", 100, 10)

    levered = PaperBroker(
        SQLiteStore(tmp_path / "levered.db"),
        initial_cash=1000,
        capabilities=RuntimeCapabilities(enable_uncovered_short=True, max_leverage=1.0),
    )
    with pytest.raises(PermissionError, match="leverage"):
        levered.submit_market_order("AMD", "short", 20_000, 100)

    allowed = PaperBroker(
        SQLiteStore(tmp_path / "allowed.db"),
        initial_cash=1000,
        capabilities=RuntimeCapabilities(enable_uncovered_short=True, max_leverage=2.0),
    )
    allowed.submit_market_order("AMD", "short", 1_000, 100)
    assert allowed.positions()["AMD"] == pytest.approx(-10.0)


def test_partial_sell_keeps_original_average_price(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    broker = PaperBroker(store, initial_cash=10_000)
    broker.submit_market_order("AMD", "buy", 1000, 100)  # 10 @ 100
    broker.submit_market_order("AMD", "sell", 440, 110)  # reduce by 4 @ 110
    row = store.connection.execute(
        "SELECT quantity, average_price FROM paper_positions WHERE symbol='AMD'"
    ).fetchone()
    assert row[0] == pytest.approx(6.0)
    assert row[1] == pytest.approx(100.0)
    store.close()


def test_flip_through_zero_reprices_at_trade(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    broker = PaperBroker(
        store,
        initial_cash=100_000,
        capabilities=RuntimeCapabilities(enable_uncovered_short=True),
    )
    broker.submit_market_order("AMD", "buy", 1000, 100)   # +10 @ 100
    broker.submit_market_order("AMD", "sell", 1650, 110)  # sell 15 -> flip to -5 @ 110
    row = store.connection.execute(
        "SELECT quantity, average_price FROM paper_positions WHERE symbol='AMD'"
    ).fetchone()
    assert row[0] == pytest.approx(-5.0, abs=1e-9)
    assert row[1] == pytest.approx(110.0)
    store.close()
