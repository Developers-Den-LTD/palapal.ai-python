from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from schema.AI_visibility_schema import AIVisibilityRequest
from services.AI_visibility_scervices import analyze_ai_visibility
from services.logger_services import logger

router = APIRouter(
    tags=["AI Visibility"],
    prefix="/api",
)





async def _run_ai_visibility_and_notify(payload: AIVisibilityRequest) -> None:
    try:
        result = analyze_ai_visibility(payload)
        logger.info(
            f"ai_visibility background: completed — status={result['status']}, "
            f"citation_score={result['citation_score']['score']}, "
            f"exposure_score={result['exposure_fairness']['score']}"
        )
    except Exception as e:
        logger.exception(f"ai_visibility background: failed — {e}")




@router.post(
    "/ai-visibility",
    status_code=status.HTTP_200_OK
)
def ai_visibility(payload: AIVisibilityRequest, background_tasks: BackgroundTasks):
    logger.info(
        f"ai_visibility route: POST /api/ai-visibility — "
        f"business='{payload.business_name}', "
        f"business_id='{payload.business_id}', "
        f"type='{payload.business_type}', loc='{payload.business_loc}'"
    )
    background_tasks.add_task(_run_ai_visibility_and_notify, payload)
    logger.info(
        f"ai_visibility route: request accepted for background processing — "
        f"business='{payload.business_name}', "
        f"business_id='{payload.business_id}', "
        f"type='{payload.business_type}', loc='{payload.business_loc}'"
    )

    return {
        "status": "accepted",
        "message": "Request is being processed in the background.",
        "business_name": payload.business_name,
        "business_id": payload.business_id,
    }
