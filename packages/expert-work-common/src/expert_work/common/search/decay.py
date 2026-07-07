"""Temporal decay weighting for recall scores — Stream CM-6 (Mini-ADR CM-G3).

Pure algorithm, no persistence dependencies. Both memory-store
implementations (SQL + in-memory) multiply their recall scores by
:func:`temporal_decay_factor` so recently-used memories win same-relevance
ties against stale ones.

The factor is floored at ``0.5`` deliberately: a canonical fact (a user
preference written months ago) must never be buried by age alone — decay
only re-ranks inside the recall candidate window, it never evicts.
"""

from __future__ import annotations

import math
from datetime import timedelta

#: Half-life of the decaying half of the score (OpenClaw memory-search
#: parity). After 30 days the factor is 0.75, after 90 days ~0.5625,
#: asymptotically approaching the floor.
DEFAULT_HALF_LIFE = timedelta(days=30)

#: The aged-out floor — an infinitely old memory keeps half its score.
DECAY_FLOOR = 0.5


def temporal_decay_factor(*, age: timedelta, half_life: timedelta = DEFAULT_HALF_LIFE) -> float:
    """Decay factor in ``(DECAY_FLOOR, 1.0]`` for a memory of ``age``.

    ``factor = 0.5 + 0.5 * 2^(-age / half_life)`` — age 0 gives 1.0, one
    half-life gives 0.75, infinity approaches 0.5. A negative ``age``
    (clock skew — ``last_used_at`` in the future) is clamped to 0.
    """
    age_ratio = max(age, timedelta(0)) / half_life
    return DECAY_FLOOR + (1.0 - DECAY_FLOOR) * math.pow(2.0, -age_ratio)


__all__ = ["DECAY_FLOOR", "DEFAULT_HALF_LIFE", "temporal_decay_factor"]
