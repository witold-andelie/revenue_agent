from revenue_agent import CapabilityGate, Insight, LongTermStrategyRegistry, RuntimeCapabilities


def test_dangerous_capabilities_are_disabled_by_default() -> None:
    gate = CapabilityGate()
    for check in (gate.require_copy_trading, gate.require_external_token, gate.require_live_broker, gate.require_uncovered_short):
        try:
            check()
        except PermissionError:
            pass
        else:
            raise AssertionError("dangerous capability was enabled by default")


def test_human_adoption_is_required_for_long_term_strategy() -> None:
    insight = Insight("i1", "AI supply chain shift", "AMD ecosystem may gain share", ("AMD",), "12m", 0.7)
    registry = LongTermStrategyRegistry()
    try:
        registry.require_adopted(insight.id)
    except PermissionError:
        pass
    else:
        raise AssertionError("unadopted insight became an active strategy")
    adoption = registry.adopt(insight, "human-1", "Reviewed filings and competitive data")
    assert adoption.human_id == "human-1"
    assert registry.require_adopted(insight.id) == adoption


def test_explicit_capability_profile_can_enable_paper_gated_features() -> None:
    gate = CapabilityGate(RuntimeCapabilities(enable_copy_trading=True, max_leverage=2.0))
    gate.require_copy_trading()
    gate.require_leverage(2.0)


def test_capability_profile_can_be_loaded_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ENABLE_LIVE_BROKER", "true")
    monkeypatch.setenv("AGENT_MAX_LEVERAGE", "1.5")
    profile = RuntimeCapabilities.from_env()
    assert profile.enable_live_broker is True
    assert profile.max_leverage == 1.5
