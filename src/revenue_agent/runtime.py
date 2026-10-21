from __future__ import annotations

from .domain import Event, FundamentalSnapshot, Insight, MarketBar
from .governance import LongTermStrategyRegistry
from .insights import InsightGenerator
from .research_pipeline import PaperSignal, ResearchPipeline
from .tooling import ToolCall, ToolPlanner, ToolRegistry, ToolResult


class AgentRuntime:
    """One safe cycle: research first, apply only human-adopted strategies."""

    def __init__(self, llm: object, profiles: tuple, strategy_registry: LongTermStrategyRegistry | None = None) -> None:
        self.insights = InsightGenerator(llm)
        self.pipeline = ResearchPipeline(profiles)
        self.strategies = strategy_registry or LongTermStrategyRegistry()

    def plan(self, llm_task: str, context: dict, registry: ToolRegistry) -> tuple[ToolCall, ...]:
        return ToolPlanner(self.insights.llm, registry).plan(llm_task, context)

    @staticmethod
    def execute_plan(calls: tuple[ToolCall, ...], registry: ToolRegistry) -> tuple[ToolResult, ...]:
        return tuple(registry.execute(call) for call in calls)

    def research(self, events: tuple[Event, ...], bars: tuple[MarketBar, ...] = (), fundamentals: tuple[FundamentalSnapshot, ...] = (), *, context: str = "") -> tuple[Insight, ...]:
        return self.insights.generate(events, context=context)

    def adopt(self, insight: Insight, human_id: str, rationale: str):
        return self.strategies.adopt(insight, human_id, rationale)

    def apply_adopted(self, insight: Insight, events: tuple[Event, ...], bars: tuple[MarketBar, ...] = (), fundamentals: tuple[FundamentalSnapshot, ...] = ()) -> tuple[PaperSignal, ...]:
        self.strategies.require_adopted(insight.id)
        return self.pipeline.run(events, bars, fundamentals)
