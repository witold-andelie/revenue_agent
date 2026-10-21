from __future__ import annotations

import sys
from dataclasses import dataclass

from .barbell import BarbellConfig, LegState, RebalanceDecision, rebalance_barbell
from .paper_broker import PaperBroker


@dataclass(frozen=True)
class RebalanceOrder:
    symbol: str
    side: str
    notional: float
    price: float


def plan_barbell_orders(
    attack: LegState,
    defense: LegState,
    current_weights: dict[str, float],
    prices: dict[str, float],
    equity: float,
    config: BarbellConfig = BarbellConfig(),
) -> tuple[RebalanceDecision, tuple[RebalanceOrder, ...]]:
    if equity <= 0:
        raise ValueError("equity must be positive")
    decision = rebalance_barbell(attack, defense, current_weights, config)
    orders = []
    for symbol, weight_delta in decision.trades.items():
        price = prices.get(symbol)
        if price is None or price <= 0 or abs(weight_delta) < 1e-12:
            continue
        orders.append(RebalanceOrder(symbol, "buy" if weight_delta > 0 else "sell", abs(weight_delta) * equity, price))
    # Sells free paper cash first so subsequent buys never trip the leverage cap mid-rebalance.
    orders.sort(key=lambda order: 0 if order.side == "sell" else 1)
    orders = _reserve_fee_budget(orders, config)
    return decision, tuple(orders)


def _reserve_fee_budget(orders: list[RebalanceOrder], config: BarbellConfig) -> list[RebalanceOrder]:
    """Shrink buys by the estimated round-trip fees so a sell→buy rotation
    cannot overdraw paper cash when the plan deploys the residual balance."""
    buys = [order for order in orders if order.side == "buy"]
    if not buys:
        return orders
    fee_rate = config.transaction_cost_bps / 10_000
    reserve = sum(order.notional for order in orders) * fee_rate
    buy_total = sum(order.notional for order in buys)
    if buy_total <= 0 or reserve <= 0:
        return orders
    scale = max(0.0, (buy_total - reserve) / buy_total)
    return [
        RebalanceOrder(order.symbol, order.side, order.notional * scale, order.price)
        if order.side == "buy" else order
        for order in orders
    ]


def execute_barbell_orders(broker: PaperBroker, orders: tuple[RebalanceOrder, ...], strategy_id: str) -> tuple[int, ...]:
    """Execute sequentially; one rejected leg (risk gate, moved market) must
    not abort the remaining legs of the rebalance."""
    fills = []
    for order in orders:
        try:
            fills.append(broker.submit_market_order(order.symbol, order.side, order.notional, order.price, strategy_id))
        except (ValueError, PermissionError, RuntimeError) as error:
            print(f"rebalance order skipped ({order.symbol} {order.side}): {error}", file=sys.stderr)
    return tuple(fills)
