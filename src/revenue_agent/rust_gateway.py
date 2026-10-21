from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any


class RustGatewayClient:
    """Client for the local Rust paper/risk process."""

    def __init__(self, binary: str | Path | None = None, *, cwd: str | Path | None = None) -> None:
        default = Path(__file__).resolve().parents[2] / "rust" / "target" / "debug" / ("revenue-gateway.exe" if os.name == "nt" else "revenue-gateway")
        self.binary = str(binary or os.getenv("REVENUE_GATEWAY_BIN", default))
        self.process = subprocess.Popen([self.binary], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)

    def close(self) -> None:
        if self.process.stdin and self.process.poll() is None:
            try:
                self.request({"type": "shutdown"})
            except (BrokenPipeError, RuntimeError):
                pass
        self.process.wait(timeout=5)

    def __enter__(self) -> "RustGatewayClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.process.poll() is not None or not self.process.stdin or not self.process.stdout:
            raise RuntimeError("Rust gateway is not running")
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("Rust gateway closed its protocol stream")
        response = json.loads(line)
        if not response.get("ok"):
            error = response.get("error", {})
            raise RuntimeError(f"Rust gateway {error.get('code', 'error')}: {error.get('message', 'request rejected')}")
        return response

    def set_quote(self, symbol: str, price: float, observed_at_epoch: int | None = None) -> None:
        self.request({"type": "quote", "symbol": symbol, "price": price, "observed_at_epoch": observed_at_epoch or int(time.time())})

    def authorize_strategy(self, strategy_id: str, human_id: str) -> None:
        self.request({"type": "authorize", "strategy_id": strategy_id, "human_id": human_id})

    def submit_market_order(self, symbol: str, side: str, notional: float, price: float, strategy_id: str) -> dict[str, Any]:
        self.set_quote(symbol, price)
        response = self.request({"type": "submit", "idempotency_key": str(uuid.uuid4()), "symbol": symbol, "side": side, "notional": notional, "strategy_id": strategy_id, "mode": "paper"})
        return response["fill"]

    def snapshot(self) -> dict[str, Any]:
        return self.request({"type": "snapshot"})["snapshot"]
