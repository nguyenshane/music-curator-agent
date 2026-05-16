from math import exp

HALF_LIFE_DAYS = 30


def decay(days_since_event: float) -> float:
    return exp(-0.69314718056 * days_since_event / HALF_LIFE_DAYS)


def rejection_penalty(feedback_score: float, penalty_scale: float = 1.0) -> float:
    return max(0.0, -feedback_score) * penalty_scale
