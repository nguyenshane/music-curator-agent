def score_track(
    *,
    taste_match: float,
    context_match: float,
    freshness: float,
    novelty: float,
    diversity: float,
    rejection_penalty: float,
) -> float:
    """Deterministic scoring function from PRD FR-7."""
    return (
        taste_match * 0.35
        + context_match * 0.25
        + freshness * 0.15
        + novelty * 0.15
        + diversity * 0.10
        - rejection_penalty
    )
