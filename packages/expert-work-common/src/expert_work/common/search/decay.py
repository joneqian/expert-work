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


#: Frequency-reinforcement cap — a heavily-recalled memory tops out at
#: 1.5x so an old-but-hot fact cannot permanently dominate the window.
FREQ_BOOST_CAP = 1.5
#: log-scale reinforcement coefficient (Ebbinghaus-style): ~10 accesses
#: ≈ 1.1x, ~100 ≈ 1.2x. Deliberately gentle — relevance stays the axis.
FREQ_BOOST_K = 0.1


def frequency_boost(access_count: int) -> float:
    """Access-reinforcement multiplier in ``[1.0, FREQ_BOOST_CAP]``.

    ``1 + log10(1 + n)·k`` capped at ``FREQ_BOOST_CAP``. ``n <= 0`` returns
    1.0 (neutral) — a never-recalled memory gets no boost, and a negative
    count (nonsensical) is clamped rather than fed to ``log10``.
    """
    if access_count <= 0:
        return 1.0
    return min(FREQ_BOOST_CAP, 1.0 + math.log10(1 + access_count) * FREQ_BOOST_K)


#: importance re-weight span — importance 0.5 (neutral) → 1.0x, 1.0 → 1.2x,
#: 0.0 → 0.8x. A tie-breaker, never enough to lift a weak-relevance memory
#: over a strong-relevance one.
IMPORTANCE_WEIGHT_SPAN = 0.4


def importance_weight(importance: float) -> float:
    """Importance re-weight in ``[0.8, 1.2]`` centred on 1.0 at importance 0.5."""
    return 1.0 + (importance - 0.5) * IMPORTANCE_WEIGHT_SPAN


__all__ = [
    "DECAY_FLOOR",
    "DEFAULT_HALF_LIFE",
    "FREQ_BOOST_CAP",
    "FREQ_BOOST_K",
    "IMPORTANCE_WEIGHT_SPAN",
    "frequency_boost",
    "importance_weight",
    "temporal_decay_factor",
]
