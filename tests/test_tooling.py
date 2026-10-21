import pytest

from revenue_agent import ToolCall, ToolRegistry, ToolPlanner


class FakePlannerLLM:
    def __init__(self, response):
        self.response = response

    def complete(self, messages):
        assert "tool planner" in messages[0]["content"]
        return self.response


def test_registry_executes_only_registered_deterministic_tools() -> None:
    registry = ToolRegistry()
    registry.register("read_quote", lambda args: {"symbol": args["symbol"], "price": 10})
    assert registry.execute(ToolCall("read_quote", {"symbol": "AMD"})).value["price"] == 10
    denied = registry.execute(ToolCall("place_order", {}))
    assert denied.ok is False


def test_planner_rejects_unallowlisted_tool() -> None:
    registry = ToolRegistry()
    registry.register("read_quote", lambda args: args)
    planner = ToolPlanner(FakePlannerLLM('{"calls":[{"name":"place_order","arguments":{}}]}'), registry)
    with pytest.raises(ValueError, match="unavailable"):
        planner.plan("trade", {})


def test_planner_returns_valid_calls() -> None:
    registry = ToolRegistry()
    registry.register("read_quote", lambda args: args)
    planner = ToolPlanner(FakePlannerLLM('{"calls":[{"name":"read_quote","arguments":{"symbol":"AMD"}}]}'), registry)
    calls = planner.plan("research", {})
    assert calls == (ToolCall("read_quote", {"symbol": "AMD"},),)


class FakeNativeToolCallLLM:
    def __init__(self, names):
        self.names = names

    def complete_response(self, messages, tools=None, max_tokens=800):
        from revenue_agent.llm_client import CompletionResponse, ToolCallResponse

        calls = tuple(ToolCallResponse(str(i), name, {}) for i, name in enumerate(self.names))
        return CompletionResponse(None, calls)


def test_native_tool_calls_are_allowlisted_and_capped() -> None:
    registry = ToolRegistry()
    registry.register("read_quote", lambda args: args)
    planner = ToolPlanner(FakeNativeToolCallLLM(["place_order", "read_quote"]), registry)
    with pytest.raises(ValueError, match="unavailable"):
        planner.plan("trade", {})
    capped = ToolPlanner(FakeNativeToolCallLLM(["read_quote"] * 9), registry)
    with pytest.raises(ValueError, match="at most 8"):
        capped.plan("trade", {})
    ok = ToolPlanner(FakeNativeToolCallLLM(["read_quote"]), registry)
    assert ok.plan("trade", {}) == (ToolCall("read_quote", {}),)
