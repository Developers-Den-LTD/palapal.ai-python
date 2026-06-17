from fastapi import APIRouter, Depends, HTTPException, status

from schema.technical_foundation_schema import TechnicalFoundationRequest
from services.logger_services import logger
from services.technical_foundation import check_technical_foundation
from utils.auth_utils import verify_secret_key

router = APIRouter(
    tags=["Technical Foundation"],
    prefix="/api",
)


@router.post(
    "/technical-foundation",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_secret_key)],
)
def technical_foundation(payload: TechnicalFoundationRequest):
    logger.info(
        "technical_foundation route: POST /api/technical-foundation — request received"
    )
    logger.info(
        f"technical_foundation route: website_url='{payload.website_url}'"
    )
    try:
        result = check_technical_foundation(payload.website_url)
        if result["status"] == "success":
            pagespeed_score = result.get("pagespeed_score", {})
            llms_txt = result.get("llms_txt", {})
            json_ld = result.get("json_ld", {})
            nap_consistency = result.get("nap_consistency", {})
            logger.info(
                "technical_foundation route: request completed successfully — "
                f"website='{result.get('website')}', "
                f"pagespeed_score={pagespeed_score.get('score')}/{pagespeed_score.get('max_score')}, "
                f"mobile_score={pagespeed_score.get('mobile', {}).get('score')}, "
                f"desktop_score={pagespeed_score.get('desktop', {}).get('score')}, "
                f"avg_lcp_pts={pagespeed_score.get('lcp_pts')}, "
                f"avg_performance_pts={pagespeed_score.get('performance_pts')}, "
                f"llms_txt_score={llms_txt.get('score')}, "
                f"llms_txt_message='{llms_txt.get('message')}', "
                f"json_ld_score={json_ld.get('score')}, "
                f"json_ld_found={json_ld.get('found')}, "
                f"json_ld_message='{json_ld.get('message')}', "
                f"nap_consistency_score={nap_consistency.get('score')}, "
                f"nap_consistent={nap_consistency.get('consistent')}, "
                f"nap_consistency_message='{nap_consistency.get('message')}', "
                f"DDI_technical_foundation_Result={result.get('DDI_technical_foundation_Result')}, "
                f"max_technical_foundation_Score={result.get('max_technical_foundation_Score')}"
            )
            for strategy_result in result.get("results", []):
                logger.info(
                    "technical_foundation route: strategy result — "
                    f"strategy='{strategy_result.get('strategy')}', "
                    f"performance={strategy_result.get('performance_score')}, "
                    f"lcp={strategy_result.get('largest_contentful_paint')}"
                )
        else:
            logger.warning(
                "technical_foundation route: request completed with error — "
                f"website='{result.get('website')}', "
                f"message={result.get('message')}"
            )
        return result
    except ValueError as e:
        logger.exception(
            f"technical_foundation route: invalid URL — website='{payload.website_url}', error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid URL format: {str(e)}",
        )
    except Exception as e:
        logger.exception(
            f"technical_foundation route: request failed — website='{payload.website_url}', error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Technical foundation check failed: {str(e)}",
        )
