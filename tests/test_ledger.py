from dataclasses import dataclass
from datetime import datetime, timezone

from revenue_agent import AuditLedger


@dataclass(frozen=True)
class Decision:
    action: str
    weight: float


def test_audit_ledger_is_hash_chained_and_detects_tampering(tmp_path) -> None:
    ledger = AuditLedger(tmp_path / "audit.jsonl")
    ledger.append("decision", Decision("paper_short", 0.2))
    ledger.append("outcome", {"revenue": 0.0})
    assert ledger.verify()

    path = tmp_path / "audit.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace("paper_short", "live_short"), encoding="utf-8")
    assert not ledger.verify()


def test_audit_ledger_serializes_event_timestamps(tmp_path) -> None:
    ledger = AuditLedger(tmp_path / "audit.jsonl")
    ledger.append("event", {"observed_at": datetime.now(timezone.utc)})
    assert ledger.verify()
