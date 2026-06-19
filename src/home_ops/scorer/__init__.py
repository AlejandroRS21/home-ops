"""Scoring engine for Home-Ops pipeline.

Provides config-driven, multi-dimensional listing scoring with
euribor-based affordability analysis, weight validation, and automatic
weight redistribution for None fields.

Key exports:
    RulesScorer: Main scoring class — receives Config via constructor DI.
    ScoreResult: Aggregated result with dimensions, flags, and total.
    DimensionScore: Per-dimension breakdown.
"""

from home_ops.scorer.models import DimensionScore, ScoreResult
from home_ops.scorer.rules import RulesScorer

__all__ = [
    "DimensionScore",
    "RulesScorer",
    "ScoreResult",
]
