from fastapi import APIRouter, HTTPException, status

from schema.keyword_seo_comparison_schema import KeywordSeoComparisonRequest
from services.keyword_seo_comparison_services import get_keyword_seo_comparison
from services.logger_services import logger

router = APIRouter(
    tags=["Keyword SEO Comparison"],
    prefix="/api",
)


@router.post(
    "/keyword-seo-comparison",
    status_code=status.HTTP_200_OK,
)
def keyword_seo_comparison(payload: KeywordSeoComparisonRequest):
    logger.info(
        "keyword_seo_comparison route: POST /api/keyword-seo-comparison — "
        f"business_id='{payload.business_id}', "
        f"competitor_ids={payload.competitor_ids}"
    )

    if not payload.competitor_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one competitor_id is required",
        )

    if payload.business_id in payload.competitor_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="business_id cannot appear in competitor_ids",
        )

    try:
        result = get_keyword_seo_comparison(
            business_id=payload.business_id,
            competitor_ids=payload.competitor_ids,
        )
        if result.get("status") == "not_found":
            logger.warning(
                "keyword_seo_comparison route: no Keyword SEO data found — "
                f"business_id='{payload.business_id}'"
            )
        return result
    except Exception as exc:
        logger.exception(f"keyword_seo_comparison route: request failed — {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Keyword SEO comparison failed: {str(exc)}",
        )
