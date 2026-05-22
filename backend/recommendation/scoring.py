"""FR-7 scoring formula.

Weights tightened in the v1.6 cut to give diversity real teeth and to make
audio similarity a first-class signal (small but present, gracefully no-op
when audio features aren't available).
"""
from __future__ import annotations


# Coefficients sum to 1.0 across the additive terms; rejection_penalty is
# subtractive and uncapped on purpose so a strong negative signal can
# dominate.
WEIGHT_TASTE = 0.30
WEIGHT_CONTEXT = 0.20
WEIGHT_FRESHNESS = 0.10
WEIGHT_NOVELTY = 0.10
WEIGHT_DIVERSITY = 0.25
WEIGHT_AUDIO = 0.05


def score_track(
    *,
    taste_match: float,
    context_match: float,
    freshness: float,
    novelty: float,
    diversity: float,
    rejection_penalty: float,
    audio_similarity: float = 0.5,
) -> float:
    """Deterministic FR-7 scorer.

    `audio_similarity` defaults to 0.5 (neutral) so tracks lacking Spotify
    audio features are not penalised vs. tracks that have them.
    """
    return (
        taste_match * WEIGHT_TASTE
        + context_match * WEIGHT_CONTEXT
        + freshness * WEIGHT_FRESHNESS
        + novelty * WEIGHT_NOVELTY
        + diversity * WEIGHT_DIVERSITY
        + audio_similarity * WEIGHT_AUDIO
        - rejection_penalty
    )
