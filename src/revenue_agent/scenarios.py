from __future__ import annotations

from .domain import Scenario


def default_scenarios() -> tuple[Scenario, ...]:
    """Current regime priors; refresh from official releases before each run.

    NOAA CPC's 2026-07-09 discussion reported 97% persistence through early
    spring 2027 and 81% probability of a very strong Oct-Dec event. Those are
    separate regime and intensity probabilities, not a claim of a guaranteed
    all-time record or uniform impact across every geography.
    """
    return (
        Scenario(
            "el_nino_active",
            0.97,
            {"agriculture": -0.7, "pharma": -0.2, "transport": -0.5, "industry": -0.3},
            "NOAA CPC (2026-07-09): El Nino is ongoing and likely persists into early 2027.",
        ),
        Scenario(
            "el_nino_very_strong_tail",
            0.81,
            {"agriculture": -0.95, "pharma": -0.3, "transport": -0.7, "industry": -0.5},
            "NOAA CPC projects an 81% chance of a very strong Oct-Dec event; local impacts remain heterogeneous.",
        ),
        Scenario(
            "ai_drawdown",
            0.35,
            {"ai_infrastructure": -0.9, "semiconductors": -0.6, "software": -0.4, "transport": -0.1},
            "Valuation compression and capex slowdown are the stress assumptions.",
        ),
        Scenario(
            "agi_acceleration",
            0.32,
            {"ai_infrastructure": 0.9, "semiconductors": 0.7, "software": 0.8, "labor_services": -0.5},
            "A capability jump could reprice compute, software, and labor substitution.",
        ),
    )


def scenario_impact(scenario: Scenario, entity_sector: str) -> float:
    return scenario.impacts.get(entity_sector, 0.0)
