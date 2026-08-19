from fastapi import APIRouter, HTTPException, status

from schema.competitor_analysis_schema import CompetitorAnalysisRequest
from services.competitor_analysis_services import get_competitor_analysis
from services.logger_services import logger

router = APIRouter(
    tags=["Competitor Analysis"],
    prefix="/api",
)


@router.post(
    "/competitor-analysis",
    status_code=status.HTTP_200_OK,
)
def competitor_analysis(payload: CompetitorAnalysisRequest):
    logger.info(
        f"competitor_analysis route: POST /api/competitor-analysis — "
        f"business_id='{payload.business_id}', "
        f"competitor_ids={payload.competitor_ids}"
    )

    if not payload.competitor_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one competitor_id is required",
        )

    try:
        return get_competitor_analysis(
            business_id=payload.business_id,
            competitor_ids=payload.competitor_ids,
        )
    except Exception as exc:
        logger.exception(f"competitor_analysis route: request failed — {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Competitor analysis failed: {str(exc)}",
        )
