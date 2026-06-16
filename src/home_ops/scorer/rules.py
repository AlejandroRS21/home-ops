"""Deterministic scoring engine based on a hard-coded rule set.

The ``DeterministicScorer`` evaluates a listing against business rules
(e.g. price thresholds, neighbourhood desirability, floor level, energy
certification) and returns a score from 0 to 100.

⚠️  **v0.1 placeholder** — ``score()`` always returns ``0.0``.  Rules
will be added in a later milestone once the scoring criteria are
finalised.
"""

from __future__ import annotations

from home_ops.models.schema import Listing


class DeterministicScorer:
    """Scores a listing using hard-coded deterministic rules.

    The scorer implements the same interface as ``GeminiVisionScorer`` so
    that the pipeline can treat them interchangeably.  The two scores are
    combined (e.g. weighted average) by the pipeline orchestrator.

    Usage::

        scorer = DeterministicScorer()
        score = scorer.score(listing)  # → 0.0 … 100.0
    """

    def score(self, listing: Listing) -> float:
        """Score a listing against the deterministic rule set.

        Args:
            listing: A ``Listing`` instance with the property data.

        Returns:
            A float between 0.0 and 100.0 representing the listing's
            desirability according to the rule set.
        """
        # TODO: implement rules once criteria are defined
        return 0.0
