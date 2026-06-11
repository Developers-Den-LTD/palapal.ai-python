from fastapi import APIRouter, Depends, HTTPException, status

from schema.AI_visibility_schema import AIVisibilityRequest
from services.AI_visibility_scervices import analyze_ai_visibility
from services.logger_services import logger
from utils.auth_utils import verify_secret_key

router = APIRouter(
    tags=["AI Visibility"],
    prefix="/api",
)


@router.post(
    "/ai-visibility",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_secret_key)],
)
def ai_visibility(payload: AIVisibilityRequest):
    logger.info(
        f"ai_visibility route: POST /api/ai-visibility — "
        f"business='{payload.business_name}', "
        f"type='{payload.business_type}', loc='{payload.business_loc}'"
    )
    try:
        result = analyze_ai_visibility(payload)
        logger.info(
            f"ai_visibility route: request completed successfully — "
            f"status={result['status']}, "
            f"citation_score={result['citation_score']['score']}, "
            f"exposure_score={result['exposure_fairness']['score']}"
        )
        return result
    except Exception as e:
        logger.exception(f"ai_visibility route: request failed — {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI visibility analysis failed: {str(e)}",
        )
