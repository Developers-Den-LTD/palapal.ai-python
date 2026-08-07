from fastapi import APIRouter, BackgroundTasks, status

from schema.review_velocity_schema import ReviewVelocityRequest
from services.Review_velocity_services import analyze_reputation_score
from services.logger_services import logger

router = APIRouter(
    tags=["Reputation Score"],
    prefix="/api",
)


def _run_review_velocity(payload: ReviewVelocityRequest) -> None:
    try:
        logger.info(
            "review_velocity background: started — "
            f"business='{payload.business_name}', business_id='{payload.business_id}'"
        )
        result = analyze_reputation_score(
            payload.business_name,
            payload.business_id,
        )
        if result["status"] == "success":
            logger.info(
                "review_velocity background: completed successfully — "
                f"business='{result.get('business')}', "
                f"total_reviews={result.get('total_reviews')}, "
                f"velocity={result['review_velocity']['score']}/{result['review_velocity']['max_score']}, "
                f"decay={result['star_rating_decay']['score']}/{result['star_rating_decay']['max_score']}, "
                f"response={result['response_rate']['score']}/{result['response_rate']['max_score']}, "
                f"DDI={result.get('DDI_Reputation_Score_Result', 0)}/{result.get('max_DDI_Reputation_Score', 40)}"
            )
        else:
            logger.warning(
                "review_velocity background: completed with error — "
                f"message={result.get('message')}"
            )
    except Exception as e:
        logger.exception(f"review_velocity background: failed — {e}")


@router.post(
    "/review-velocity",
    status_code=status.HTTP_202_ACCEPTED,
)
def review_velocity(
    payload: ReviewVelocityRequest,
    background_tasks: BackgroundTasks,
):
    logger.info(
        "review_velocity route: POST /api/review-velocity — request received "
        f"business='{payload.business_name}', business_id='{payload.business_id}'"
    )

    background_tasks.add_task(_run_review_velocity, payload)
    logger.info(
        "review_velocity route: request accepted for background processing — "
        f"business='{payload.business_name}', business_id='{payload.business_id}'"
    )

    return {
        "status": "accepted",
        "message": "Reputation score analysis started in background.",
        "business_name": payload.business_name,
        "business_id": payload.business_id,
    }