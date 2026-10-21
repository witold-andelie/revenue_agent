from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Protocol

from .domain import Insight


@dataclass(frozen=True)
class RuntimeCapabilities:
    """Dangerous capabilities are opt-in and remain off in the default profile."""

    enable_copy_trading: bool = False
    enable_external_platform_tokens: bool = False
    enable_live_broker: bool = False
    enable_uncovered_short: bool = False
    max_leverage: float = 1.0
    require_human_strategy_adoption: bool = True

    @classmethod
    def from_env(cls) -> "RuntimeCapabilities":
        def flag(name: str, default: bool = False) -> bool:
            return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            enable_copy_trading=flag("AGENT_ENABLE_COPY_TRADING"),
            enable_external_platform_tokens=flag("AGENT_ENABLE_EXTERNAL_TOKENS"),
            enable_live_broker=flag("AGENT_ENABLE_LIVE_BROKER"),
            enable_uncovered_short=flag("AGENT_ENABLE_UNCOVERED_SHORT"),
            max_leverage=float(os.getenv("AGENT_MAX_LEVERAGE", "1.0")),
            require_human_strategy_adoption=flag("AGENT_REQUIRE_HUMAN_ADOPTION", True),
        )


class CapabilityGate:
    def __init__(self, capabilities: RuntimeCapabilities = RuntimeCapabilities()) -> None:
        if capabilities.max_leverage < 1.0:
            raise ValueError("max_leverage must be at least 1.0")
        self.capabilities = capabilities

    def require_copy_trading(self) -> None:
        self._require(self.capabilities.enable_copy_trading, "copy trading")

    def require_external_token(self) -> None:
        self._require(self.capabilities.enable_external_platform_tokens, "external platform token")

    def require_live_broker(self) -> None:
        self._require(self.capabilities.enable_live_broker, "live broker")

    def require_uncovered_short(self) -> None:
        self._require(self.capabilities.enable_uncovered_short, "uncovered short")

    def require_leverage(self, requested: float) -> None:
        if requested > self.capabilities.max_leverage:
            raise PermissionError(f"leverage {requested} exceeds configured cap {self.capabilities.max_leverage}")

    @staticmethod
    def _require(enabled: bool, capability: str) -> None:
        if not enabled:
            raise PermissionError(f"{capability} is disabled by runtime policy")


class BrokerAdapter(Protocol):
    def submit_order(self, order: dict) -> dict:
        ...

    def cancel_order(self, order_id: str) -> None:
        ...


class CopyTradingAdapter(Protocol):
    def copy_signal(self, signal: dict) -> dict:
        ...


class ExternalPlatformAdapter(Protocol):
    def publish_signal(self, signal: dict, token: str) -> dict:
        ...


@dataclass(frozen=True)
class StrategyAdoption:
    insight_id: str
    human_id: str
    adopted_at: datetime
    rationale: str


class LongTermStrategyRegistry:
    """Only human-adopted insights can become durable trading strategies."""

    def __init__(self, capabilities: RuntimeCapabilities = RuntimeCapabilities()) -> None:
        self.capabilities = capabilities
        self._adoptions: dict[str, StrategyAdoption] = {}

    def adopt(self, insight: Insight, human_id: str, rationale: str) -> StrategyAdoption:
        if not human_id.strip():
            raise ValueError("human_id is required")
        if not rationale.strip():
            raise ValueError("rationale is required")
        adoption = StrategyAdoption(insight.id, human_id, datetime.now(timezone.utc), rationale)
        self._adoptions[insight.id] = adoption
        return adoption

    def require_adopted(self, insight_id: str) -> StrategyAdoption | None:
        adoption = self._adoptions.get(insight_id)
        if self.capabilities.require_human_strategy_adoption and adoption is None:
            raise PermissionError(f"insight {insight_id} has not been adopted by a human")
        return adoption

    def is_adopted(self, insight_id: str) -> bool:
        return insight_id in self._adoptions
