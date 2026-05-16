from fastapi import APIRouter

from backend.recommendation.scoring import score_track

router = APIRouter()


@router.get("/score")
def score_sample() -> dict[str, float]:
    score = score_track(
        taste_match=0.8,
        context_match=0.7,
        freshness=0.5,
        novelty=0.6,
        diversity=0.4,
        rejection_penalty=0.1,
    )
    return {"score": score}
