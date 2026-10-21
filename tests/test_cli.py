import pytest

from revenue_agent.cli import _key_value, main
from revenue_agent.domain import Insight
from revenue_agent.storage import SQLiteStore


def test_cli_status_uses_durable_store(tmp_path, capsys) -> None:
    assert main(["--db", str(tmp_path / "agent.db"), "status"]) == 0
    assert "paper_orders" in capsys.readouterr().out


def test_paper_order_requires_an_adopted_strategy_id(tmp_path) -> None:
    db = str(tmp_path / "agent.db")
    with pytest.raises(SystemExit):
        main(["--db", db, "paper-order", "--symbol", "AMD", "--side", "buy", "--notional", "100", "--price", "10"])
    with pytest.raises(PermissionError, match="not been adopted"):
        main(["--db", db, "paper-order", "--symbol", "AMD", "--side", "buy", "--notional", "100", "--price", "10", "--strategy-id", "missing"])


def _seed_insight(db: str, insight_id: str) -> None:
    store = SQLiteStore(db)
    store.save_insight(Insight(
        id=insight_id, title="t", thesis="th", affected_entities=("AMD",), horizon="12m",
        confidence=0.5, evidence_ids=("e1",), direction="long",
    ))
    store.close()


def test_adopted_strategy_can_place_paper_order_and_leaves_audit_trail(tmp_path) -> None:
    db = str(tmp_path / "agent.db")
    _seed_insight(db, "insight-1")
    assert main(["--db", db, "adopt", "--insight-id", "insight-1", "--human-id", "owner", "--rationale", "reviewed"]) == 0
    assert main(["--db", db, "paper-order", "--symbol", "AMD", "--side", "buy", "--notional", "100", "--price", "10", "--strategy-id", "insight-1"]) == 0
    audit = (tmp_path / "agent.audit.jsonl").read_text(encoding="utf-8")
    assert '"paper_order_intent"' in audit and '"paper_order_result"' in audit


def test_cli_paper_order_and_mark(tmp_path, capsys) -> None:
    db = str(tmp_path / "agent.db")
    _seed_insight(db, "insight-2")
    assert main(["--db", db, "adopt", "--insight-id", "insight-2", "--human-id", "o", "--rationale", "r"]) == 0
    assert main(["--db", db, "paper-order", "--symbol", "AMD", "--side", "buy", "--notional", "100", "--price", "10", "--strategy-id", "insight-2"]) == 0
    assert main(["--db", db, "mark", "--price", "AMD=11"]) == 0
    assert "equity" in capsys.readouterr().out


def test_price_argument_is_normalized() -> None:
    assert _key_value("amd=10") == ("AMD", 10.0)
