from __future__ import annotations

import argparse
import os
from pathlib import Path

from .config import load_dotenv, opencode_go_config
from .governance import RuntimeCapabilities
from .ledger import AuditLedger
from .learning import update_strategy_from_equity
from .domain import Insight
from .moltbook_source import MoltbookEventSource, local_moltbook_client
from .paper_broker import PaperBroker
from .barbell import LegState
from .rebalancer import execute_barbell_orders, plan_barbell_orders
from .replay import ReplayRunner
from .research_pipeline import AssetProfile
from .scheduler import AgentScheduler, ScheduleConfig
from .rust_gateway import RustGatewayClient
from .runtime import AgentRuntime
from .llm_client import LLMClient
from .storage import SQLiteStore
from .yahoo_source import YahooFinanceSource


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        return _repl(argparse.Namespace(db=args.db, initial_cash=args.initial_cash))
    if args.command == "status":
        return _status(args.db)
    if args.command == "paper-order":
        return _paper_order(args)
    if args.command == "mark":
        return _mark(args)
    if args.command == "cycle":
        return _cycle(args)
    if args.command == "insight":
        return _show_insight(args)
    if args.command == "adopt":
        return _adopt(args)
    if args.command == "replay":
        return _replay(args)
    if args.command == "rebalance":
        return _rebalance(args)
    if args.command == "schedule":
        return _schedule(args)
    if args.command == "repl":
        return _repl(args)
    if args.command == "paper-month":
        return _paper_month(args)
    parser.print_help()
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="revenue-agent")
    parser.add_argument("--db", default="data/agent.db")
    parser.add_argument("--initial-cash", type=float, default=1000.0)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("status")
    order = commands.add_parser("paper-order")
    order.add_argument("--symbol", required=True)
    order.add_argument("--side", choices=("buy", "sell", "short", "cover"), required=True)
    order.add_argument("--notional", type=float, required=True)
    order.add_argument("--price", type=float, required=True)
    order.add_argument("--strategy-id", required=True, help="insight id adopted by a human; orders without adoption are rejected")
    order.add_argument("--rust-broker", action="store_true")
    mark = commands.add_parser("mark")
    mark.add_argument("--price", action="append", required=True, help="SYMBOL=PRICE")
    cycle = commands.add_parser("cycle")
    cycle.add_argument("--symbols", nargs="+", default=["AMD", "NVDA"])
    cycle.add_argument("--period", default="1mo")
    cycle.add_argument("--moltbook", action="store_true")
    cycle.add_argument("--moltbook-root", default="../moltbook_scraper")
    cycle.add_argument("--moltbook-max-posts", type=int, default=20)
    insight = commands.add_parser("insight")
    insight.add_argument("--insight-id", required=True)
    adopt = commands.add_parser("adopt")
    adopt.add_argument("--insight-id", required=True)
    adopt.add_argument("--human-id", required=True)
    adopt.add_argument("--rationale", required=True)
    replay = commands.add_parser("replay")
    replay.add_argument("--symbols", nargs="+", required=True)
    rebalance = commands.add_parser("rebalance")
    rebalance.add_argument("--attack-symbol", required=True)
    rebalance.add_argument("--defense-symbol", required=True)
    rebalance.add_argument("--strategy-id", required=True)
    rebalance.add_argument("--rust-broker", action="store_true")
    rebalance.add_argument("--price", action="append", required=True, help="SYMBOL=PRICE")
    rebalance.add_argument("--attack-return", type=float, default=0.0)
    rebalance.add_argument("--defense-return", type=float, default=0.0)
    rebalance.add_argument("--attack-crowding", type=float, default=0.0)
    rebalance.add_argument("--defense-crowding", type=float, default=0.0)
    rebalance.add_argument("--attack-valuation", type=float, default=0.5)
    rebalance.add_argument("--defense-valuation", type=float, default=0.5)
    rebalance.add_argument("--attack-support", type=float, default=0.5)
    rebalance.add_argument("--defense-support", type=float, default=0.5)
    schedule = commands.add_parser("schedule")
    schedule.add_argument("--interval-seconds", type=float, default=1800.0)
    schedule.add_argument("--once", action="store_true")
    schedule.add_argument("--symbols", nargs="+", default=["AMD", "NVDA"])
    schedule.add_argument("--period", default="1mo")
    schedule.add_argument("--moltbook", action="store_true")
    schedule.add_argument("--moltbook-root", default="../moltbook_scraper")
    schedule.add_argument("--moltbook-max-posts", type=int, default=20)
    commands.add_parser("repl")
    month = commands.add_parser("paper-month")
    month.add_argument("--duration-days", type=float, default=30.0)
    month.add_argument("--interval-seconds", type=float, default=86400.0)
    month.add_argument("--symbols", nargs="+", default=["AMD", "NVDA"])
    month.add_argument("--period", default="1mo")
    month.add_argument("--moltbook", action="store_true")
    month.add_argument("--moltbook-root", default="../moltbook_scraper")
    month.add_argument("--moltbook-max-posts", type=int, default=20)
    month.add_argument("--once", action="store_true")
    return parser


def _status(db_path: str) -> int:
    store = SQLiteStore(db_path)
    try:
        print({"db": str(Path(db_path)), **store.counts()})
    finally:
        store.close()
    return 0


def _paper_order(args: argparse.Namespace) -> int:
    if not args.strategy_id:
        raise PermissionError("paper-order requires an adopted --strategy-id")
    if args.rust_broker:
        authorization_store = SQLiteStore(args.db)
        human_id = authorization_store.adoption_human_id(args.strategy_id)
        authorization_store.close()
        if not human_id:
            raise PermissionError(f"strategy {args.strategy_id} has not been adopted by a human")
        with RustGatewayClient() as broker:
            broker.authorize_strategy(args.strategy_id, human_id)
            fill = broker.submit_market_order(args.symbol, args.side, args.notional, args.price, args.strategy_id)
            print({"fill": fill, "mode": "rust-paper"})
        return 0
    store = SQLiteStore(args.db)
    ledger = AuditLedger(Path(args.db).with_suffix(".audit.jsonl"))
    try:
        if not store.has_adoption(args.strategy_id):
            raise PermissionError(f"strategy {args.strategy_id} has not been adopted by a human")
        intent = {
            "symbol": args.symbol, "side": args.side,
            "notional": args.notional, "price": args.price, "strategy_id": args.strategy_id,
        }
        ledger.append("paper_order_intent", intent)
        order_id = PaperBroker(store, initial_cash=args.initial_cash, capabilities=RuntimeCapabilities.from_env()).submit_market_order(args.symbol, args.side, args.notional, args.price, args.strategy_id)
        ledger.append("paper_order_result", {"order_id": order_id, **intent, "status": "filled"})
        print({"order_id": order_id, "status": "filled", "mode": "paper"})
    finally:
        store.close()
    return 0


def _mark(args: argparse.Namespace) -> int:
    prices = {_key_value(item) for item in args.price}
    store = SQLiteStore(args.db)
    try:
        print(PaperBroker(store, initial_cash=args.initial_cash, capabilities=RuntimeCapabilities.from_env()).mark_to_market(dict(prices)))
    finally:
        store.close()
    return 0


def _cycle(args: argparse.Namespace) -> int:
    store = SQLiteStore(args.db)
    try:
        yahoo = YahooFinanceSource(tuple(args.symbols), period=args.period)
        bars = yahoo.fetch()
        fundamentals = yahoo.fetch_fundamentals()
        store.save_bars(bars)
        store.save_fundamentals(fundamentals)
        events = _moltbook_events(args) if args.moltbook else ()
        if events:
            store.save_events(events)
            config = opencode_go_config()
            runtime = AgentRuntime(
                LLMClient(config["api_key"], config["base_url"], config["model"], config["timeout"]),
                tuple(AssetProfile(symbol, "unknown") for symbol in args.symbols),
            )
            insights = runtime.research(events, bars, fundamentals, context="Generate long-horizon research insights only.")
            for insight in insights:
                store.save_insight(insight)
            AuditLedger(Path(args.db).with_suffix(".audit.jsonl")).append("research_cycle", {
                "events": len(events), "insights": [insight.id for insight in insights],
            })
            print({"events": len(events), "insights": len(insights), "requires_human_adoption": True})
        else:
            print({"events": 0, "insights": 0, "message": "Yahoo data saved; no Moltbook events supplied"})
        latest = {symbol: max((bar for bar in bars if bar.symbol == symbol), key=lambda bar: bar.observed_at).close for symbol in args.symbols if any(bar.symbol == symbol for bar in bars)}
        if latest:
            try:
                print({"mark": PaperBroker(store, initial_cash=args.initial_cash, capabilities=RuntimeCapabilities.from_env()).mark_to_market(latest)})
            except ValueError as error:
                print({"mark_skipped": str(error)})
    finally:
        store.close()
    return 0


def _moltbook_events(args: argparse.Namespace):
    api_key = os.getenv("MOLTBOOK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MOLTBOOK_API_KEY is required for --moltbook")
    client = local_moltbook_client(api_key, args.moltbook_root)
    focus_terms = tuple(filter(None, os.getenv("MOLTBOOK_FOCUS_X_ACCOUNTS", "").split(",")))
    return MoltbookEventSource(client, max_posts=args.moltbook_max_posts, focus_terms=focus_terms).fetch()


def _show_insight(args: argparse.Namespace) -> int:
    store = SQLiteStore(args.db)
    try:
        insight = store.get_insight(args.insight_id)
        if insight is None:
            raise ValueError(f"insight {args.insight_id} was not found")
        print(insight)
    finally:
        store.close()
    return 0


def _adopt(args: argparse.Namespace) -> int:
    store = SQLiteStore(args.db)
    try:
        insight = store.get_insight(args.insight_id)
        if insight is None:
            raise ValueError(f"insight {args.insight_id} was not found")
        from .governance import LongTermStrategyRegistry
        adoption = LongTermStrategyRegistry().adopt(insight, args.human_id, args.rationale)
        store.save_adoption(adoption)
        AuditLedger(Path(args.db).with_suffix(".audit.jsonl")).append("strategy_adoption", adoption)
        print({"insight_id": insight.id, "adopted": True, "human_id": adoption.human_id})
    finally:
        store.close()
    return 0


def _replay(args: argparse.Namespace) -> int:
    store = SQLiteStore(args.db)
    try:
        result = ReplayRunner(tuple(AssetProfile(symbol, "unknown") for symbol in args.symbols)).run(
            store.load_events(), store.load_bars(), store.load_fundamentals()
        )
        print({"events": result.events_seen, "signals": len(result.signals), "signals_by_symbol": {symbol: sum(signal.symbol == symbol for signal in result.signals) for symbol in args.symbols}})
    finally:
        store.close()
    return 0


def _rebalance(args: argparse.Namespace) -> int:
    prices = dict(_key_value(item) for item in args.price)
    if args.rust_broker:
        authorization_store = SQLiteStore(args.db)
        human_id = authorization_store.adoption_human_id(args.strategy_id)
        authorization_store.close()
        if not human_id:
            raise PermissionError(f"strategy {args.strategy_id} has not been adopted by a human")
        with RustGatewayClient() as broker:
            broker.authorize_strategy(args.strategy_id, human_id)
            for symbol, price in prices.items():
                broker.set_quote(symbol, price)
            snapshot = broker.snapshot()
            weights = {symbol: position["quantity"] * prices[symbol] / snapshot["equity"] for symbol, position in snapshot["positions"].items() if symbol in prices and snapshot["equity"] > 0}
            attack = LegState(args.attack_symbol, "attack", args.attack_return, args.attack_crowding, args.attack_valuation, args.attack_support)
            defense = LegState(args.defense_symbol, "defense", args.defense_return, args.defense_crowding, args.defense_valuation, args.defense_support)
            decision, orders = plan_barbell_orders(attack, defense, weights, prices, snapshot["equity"])
            fills = execute_barbell_orders(broker, orders, args.strategy_id)
            print({"fills": fills, "target_weights": decision.target_weights, "risk_flags": decision.risk_flags, "mode": "rust-paper"})
        return 0
    store = SQLiteStore(args.db)
    try:
        if not store.has_adoption(args.strategy_id):
            raise PermissionError(f"strategy {args.strategy_id} has not been adopted by a human")
        broker = PaperBroker(store, initial_cash=args.initial_cash, capabilities=RuntimeCapabilities.from_env())
        snapshot = broker.snapshot(prices)
        attack = LegState(args.attack_symbol, "attack", args.attack_return, args.attack_crowding, args.attack_valuation, args.attack_support)
        defense = LegState(args.defense_symbol, "defense", args.defense_return, args.defense_crowding, args.defense_valuation, args.defense_support)
        decision, orders = plan_barbell_orders(attack, defense, snapshot["weights"], prices, snapshot["equity"])
        ledger = AuditLedger(Path(args.db).with_suffix(".audit.jsonl"))
        ledger.append("barbell_rebalance_intent", {
            "strategy_id": args.strategy_id, "decision": decision,
            "orders": orders,
        })
        order_ids = execute_barbell_orders(broker, orders, args.strategy_id)
        ledger.append("barbell_rebalance_result", {"strategy_id": args.strategy_id, "order_ids": order_ids})
        print({"orders": len(order_ids), "order_ids": order_ids, "target_weights": decision.target_weights, "risk_flags": decision.risk_flags})
    finally:
        store.close()
    return 0


def _schedule(args: argparse.Namespace) -> int:
    cycle_args = argparse.Namespace(db=args.db, initial_cash=args.initial_cash, symbols=args.symbols, period=args.period, moltbook=args.moltbook, moltbook_root=args.moltbook_root, moltbook_max_posts=args.moltbook_max_posts)
    scheduler = AgentScheduler(lambda: _cycle(cycle_args), ScheduleConfig(args.interval_seconds))
    if args.once:
        scheduler.run_once()
        return 0
    scheduler.start()
    try:
        scheduler._thread.join()
    except KeyboardInterrupt:
        scheduler.stop()
    return 0


def _paper_month(args: argparse.Namespace) -> int:
    cycle_args = argparse.Namespace(db=args.db, initial_cash=args.initial_cash, symbols=args.symbols, period=args.period, moltbook=args.moltbook, moltbook_root=args.moltbook_root, moltbook_max_posts=args.moltbook_max_posts)

    def cycle_and_learn() -> None:
        _cycle(cycle_args)
        store = SQLiteStore(args.db)
        try:
            try:
                update = update_strategy_from_equity(store)
            except ValueError as error:
                print({"learning_skipped": str(error)})
            else:
                print({"learning": update})
        finally:
            store.close()

    if args.once:
        cycle_and_learn()
        return 0
    scheduler = AgentScheduler(cycle_and_learn, ScheduleConfig(args.interval_seconds))
    scheduler.start()
    try:
        scheduler._thread.join(args.duration_days * 86400)
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.stop()
    return 0


def _repl(args: argparse.Namespace) -> int:
    print("Revenue Agent | paper-only | 初始资金 %.2f EUR" % args.initial_cash)
    print("输入 /help 查看命令。你也可以直接说：查看状态、运行研究、学习策略。")
    while True:
        try:
            # Piped input on Windows arrives with a UTF-8 BOM on the first
            # line; strip it so scripted REPL sessions parse correctly.
            line = input("agent> ").strip().lstrip("\ufeff").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return 0
        if not line:
            continue
        line = line.strip()
        if line.startswith("/"):
            line = line[1:].strip()
        parts = line.split()
        command = parts[0].lower()
        try:
            if command in {"quit", "exit", "q"}:
                print("已退出。")
                return 0
            if command in {"help", "h", "?"}:
                print("命令:")
                print("  /status                 查看账户、收益和学习状态")
                print("  /cycle [AMD NVDA]       获取数据并运行研究 cycle")
                print("  /insights               查看最新洞察")
                print("  /learn                  根据 paper 收益调整策略")
                print("  /order SYMBOL SIDE AMOUNT PRICE STRATEGY_ID")
                print("  /config                 查看当前运行配置")
                print("  /quit                   退出 agent")
                continue
            if command == "status":
                _status(args.db)
                store = SQLiteStore(args.db)
                print(_friendly_learning(store.learning_state()))
                store.close()
                continue
            if command == "cycle":
                symbols = parts[1:] or ["AMD", "NVDA"]
                _cycle(argparse.Namespace(db=args.db, initial_cash=args.initial_cash, symbols=symbols, period="1mo", moltbook=bool(os.getenv("MOLTBOOK_API_KEY")), moltbook_root="../moltbook_scraper", moltbook_max_posts=20))
                continue
            if command == "learn":
                store = SQLiteStore(args.db)
                try:
                    print(_friendly_learning(store.learning_state(), update_strategy_from_equity(store)))
                finally:
                    store.close()
                continue
            if command == "config":
                print({"db": args.db, "initial_cash_eur": args.initial_cash, "mode": "paper-only", "moltbook_configured": bool(os.getenv("MOLTBOOK_API_KEY")), "rust_gateway": bool(os.getenv("REVENUE_GATEWAY_BIN"))})
                continue
            if command == "insights":
                store = SQLiteStore(args.db)
                rows = store.connection.execute("SELECT id, title, confidence, direction FROM insights ORDER BY id").fetchall()
                print([dict(row) for row in rows])
                store.close()
                continue
            if command == "order" and len(parts) == 6:
                _paper_order(argparse.Namespace(db=args.db, initial_cash=args.initial_cash, rust_broker=False, symbol=parts[1], side=parts[2], notional=float(parts[3]), price=float(parts[4]), strategy_id=parts[5]))
                continue
            natural = line.casefold()
            if any(word in natural for word in ("状态", "账户", "status")):
                _status(args.db)
            elif any(word in natural for word in ("学习", "优化", "收益", "回撤", "learn")):
                store = SQLiteStore(args.db)
                try:
                    print(_friendly_learning(store.learning_state(), update_strategy_from_equity(store)))
                finally:
                    store.close()
            elif any(word in natural for word in ("研究", "抓取", "行情", "cycle")):
                _cycle(argparse.Namespace(db=args.db, initial_cash=args.initial_cash, symbols=["AMD", "NVDA"], period="1mo", moltbook=bool(os.getenv("MOLTBOOK_API_KEY")), moltbook_root="../moltbook_scraper", moltbook_max_posts=20))
            else:
                print("我还不理解这句话。输入 /help 查看我能执行的操作。")
        except Exception as error:
            print(f"操作未执行: {error}")


def _friendly_learning(state: dict | None, update: object | None = None) -> str:
    if update is not None:
        return "学习完成: 进攻权重 %.1f%% | 防御权重 %.1f%% | 收益 %.2f%% | 最大回撤 %.2f%% | %s" % (update.attack_weight * 100, update.defense_weight * 100, update.return_rate * 100, update.max_drawdown * 100, update.reason)
    return "学习状态: " + (str(state) if state else "还没有足够的 paper mark 数据")


def _key_value(value: str) -> tuple[str, float]:
    if "=" not in value:
        raise ValueError("price must use SYMBOL=PRICE")
    symbol, price = value.split("=", 1)
    return symbol.upper(), float(price)
