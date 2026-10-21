from __future__ import annotations

from collections import defaultdict

from .domain import Opportunity, Score


class OpportunityScorer:
    """Small scoring seam: heuristic and attention versions can be A/B tested."""

    def score(self, opportunity: Opportunity) -> Score:
        raise NotImplementedError


class HeuristicScorer(OpportunityScorer):
    def score(self, opportunity: Opportunity) -> Score:
        if not opportunity.evidence:
            return Score(opportunity.id, 0.0, 0.0, -opportunity.execution_cost)
        weighted = [e.relevance * e.direction * e.confidence for e in opportunity.evidence]
        confidence = sum(e.confidence for e in opportunity.evidence) / len(weighted)
        edge = opportunity.expected_edge + sum(weighted) / len(weighted) - opportunity.execution_cost
        return Score(opportunity.id, edge, confidence, edge, reasons=tuple(e.rationale for e in opportunity.evidence))


class AttentionScorer(OpportunityScorer):
    """Deterministic attention baseline inspired by GAT, not a trained model."""

    def __init__(self, source_weights: dict[str, float] | None = None) -> None:
        self.source_weights = source_weights or {}

    def score(self, opportunity: Opportunity) -> Score:
        if not opportunity.evidence:
            return Score(opportunity.id, 0.0, 0.0, -opportunity.execution_cost)
        logits = []
        for evidence in opportunity.evidence:
            source_weight = self.source_weights.get(evidence.event_id, 1.0)
            logits.append(max(0.0, source_weight * evidence.relevance * evidence.confidence))
        total = sum(logits) or 1.0
        attention = {e.event_id: value / total for e, value in zip(opportunity.evidence, logits)}
        signal = sum(attention[e.event_id] * e.direction * e.confidence for e in opportunity.evidence)
        confidence = sum(attention[e.event_id] * e.confidence for e in opportunity.evidence)
        edge = opportunity.expected_edge + signal - opportunity.execution_cost
        return Score(opportunity.id, edge, confidence, edge, attention=attention,
                     reasons=tuple(e.rationale for e in opportunity.evidence))
