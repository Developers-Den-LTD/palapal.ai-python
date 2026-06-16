from fastapi import APIRouter, Depends, HTTPException, status

from services.Review_velocity_services import analyze_reputation_score
from services.logger_services import logger
from utils.auth_utils import verify_secret_key

router = APIRouter(
    tags=["Reputation Score"],
    prefix="/api",
)


@router.post(
    "/review-velocity",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_secret_key)],
)
def review_velocity():
    logger.info("review_velocity route: POST /api/review-velocity — request received")
    try:
        result = analyze_reputation_score()
        if result["status"] == "success":
            logger.info(
                "review_velocity route: request completed successfully — "
                f"business='{result.get('business')}', "
                f"total_reviews={result.get('total_reviews')}, "
                f"velocity={result['review_velocity']['score']}/{result['review_velocity']['max_score']}, "
                f"decay={result['star_rating_decay']['score']}/{result['star_rating_decay']['max_score']}, "
                f"response={result['response_rate']['score']}/{result['response_rate']['max_score']}, "
                f"DDI={result.get('DDI_Reputation_Score_Result', 0)}/{result.get('max_DDI_Reputation_Score', 40)}"
            )
        else:
            logger.warning(
                f"review_velocity route: request completed with error — "
                f"message={result.get('message')}"
            )
        return result
    except Exception as e:
        logger.exception(f"review_velocity route: request failed — {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Reputation score analysis failed: {str(e)}",
        )
