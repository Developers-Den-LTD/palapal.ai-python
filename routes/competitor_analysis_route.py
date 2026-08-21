from fastapi import APIRouter, HTTPException, status

from schema.competitor_analysis_schema import CompetitorAnalysisRequest
from schema.competitor_names_schema import CompetitorNamesRequest
from services.competitor_analysis_services import get_competitor_analysis
from services.competitor_names_services import get_top_competitor_names
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


@router.post(
    "/competitor-names",
    status_code=status.HTTP_200_OK,
)
def competitor_names(payload: CompetitorNamesRequest):
    logger.info(
        f"competitor_names route: POST /api/competitor-names — "
        f"business='{payload.business_name}', business_id='{payload.business_id}'"
    )

    try:
        result = get_top_competitor_names(
            business_name=payload.business_name,
            business_id=payload.business_id,
        )
        if result.get("status") != "success":
            logger.warning(
                f"competitor_names route: completed with error — "
                f"message={result.get('message')}"
            )
        return result
    except Exception as exc:
        logger.exception(f"competitor_names route: request failed — {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Competitor names lookup failed: {str(exc)}",
        )
