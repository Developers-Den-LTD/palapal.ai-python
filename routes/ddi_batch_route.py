from fastapi import APIRouter, HTTPException, status

from schema.ddi_batch_schema import DDIBatchBusinessItem
from services.ddi_batch_services import MAX_BATCH_SIZE, get_ddi_scores_for_businesses
from services.logger_services import logger

router = APIRouter(
    tags=["DDI Score Batch"],
    prefix="/api",
)


@router.post(
    "/ddi-score-batch",
    status_code=status.HTTP_200_OK,
)
def ddi_score_batch(payload: list[DDIBatchBusinessItem]):
    logger.info(
        f"ddi_batch route: POST /api/ddi-score-batch — count={len(payload)}"
    )

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one business is required",
        )

    if len(payload) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A maximum of {MAX_BATCH_SIZE} businesses is allowed per request",
        )

    try:
        return get_ddi_scores_for_businesses(payload)
    except Exception as e:
        logger.exception(f"ddi_batch route: request failed — {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"DDI score batch lookup failed: {str(e)}",
        )
