from __future__ import annotations

from .domain import Position, Scenario


def build_hedged_book(
    scenario: Scenario,
    sector_entities: dict[str, tuple[str, ...]],
    gross_limit: float = 1.0,
    max_position: float = 0.25,
) -> tuple[Position, ...]:
    """Return paper positions only. Negative weight means a proposed short."""
    if not 0 < gross_limit <= 1 or max_position <= 0:
        raise ValueError("invalid portfolio limits")
    ranked = sorted(scenario.impacts.items(), key=lambda item: abs(item[1]), reverse=True)
    positions: list[Position] = []
    for sector, impact in ranked:
        entities = sector_entities.get(sector, ())
        if not entities or impact == 0:
            continue
        direction = "long" if impact > 0 else "short"
        per_entity = min(max_position, gross_limit / max(len(entities), 1))
        for entity in entities:
            positions.append(Position(entity, direction, per_entity,
                                      f"Scenario {scenario.name}: {sector} impact {impact:+.2f}",
                                      hedge_for=(sector,)))
    total = sum(position.weight for position in positions)
    if total > gross_limit and positions:
        scale = gross_limit / total
        positions = [Position(p.entity, p.direction, p.weight * scale, p.thesis, p.hedge_for) for p in positions]
    return tuple(positions)
