from backend.feedback.scoring import decay, rejection_penalty
from backend.recommendation.scoring import score_track


def test_score_track_formula():
    score = score_track(
        taste_match=1.0,
        context_match=1.0,
        freshness=1.0,
        novelty=1.0,
        diversity=1.0,
        rejection_penalty=0.0,
    )
    assert score == 1.0


def test_feedback_decay_and_penalty():
    assert decay(0) == 1.0
    assert 0.49 < decay(30) < 0.51
    assert rejection_penalty(-2.0, penalty_scale=1.5) == 3.0
