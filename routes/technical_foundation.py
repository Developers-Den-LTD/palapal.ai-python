# from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks

# from schema.technical_foundation_schema import TechnicalFoundationRequest
# from services.logger_services import logger
# from services.technical_foundation import check_technical_foundation

# router = APIRouter(
#     tags=["Technical Foundation"],
#     prefix="/api",
# )


# def run_technical_foundation(
#     website_url: str,
#     business_name: str,
#     business_id: str | int | None = None,
# ):
#     try:
#         logger.info(
#             f"technical_foundation background: started — "
#             f"website='{website_url}', business='{business_name}', "
#             f"business_id='{business_id}'"
#         )

#         result = check_technical_foundation(
#             website_url,
#             business_name,
#             business_id,
#         )

#         if result["status"] == "success":
#             pagespeed_score = result.get("pagespeed_score", {})
#             llms_txt = result.get("llms_txt", {})
#             json_ld = result.get("json_ld", {})
#             logger.info(
#                 "technical_foundation background: completed successfully — "
#                 f"website='{result.get('website')}', "
#                 f"pagespeed_score={pagespeed_score.get('score')}/{pagespeed_score.get('max_score')}, "
#                 f"mobile_score={pagespeed_score.get('mobile', {}).get('score')}, "
#                 f"desktop_score={pagespeed_score.get('desktop', {}).get('score')}, "
#                 f"avg_lcp_pts={pagespeed_score.get('lcp_pts')}, "
#                 f"avg_performance_pts={pagespeed_score.get('performance_pts')}, "
#                 f"llms_txt_score={llms_txt.get('score')}, "
#                 f"llms_txt_message='{llms_txt.get('message')}', "
#                 f"json_ld_score={json_ld.get('score')}, "
#                 f"json_ld_found={json_ld.get('found')}, "
#                 f"json_ld_message='{json_ld.get('message')}'"
#             )
#             for strategy_result in result.get("results", []):
#                 logger.info(
#                     "technical_foundation route: strategy result — "
#                     f"strategy='{strategy_result.get('strategy')}', "
#                     f"performance={strategy_result.get('performance_score')}, "
#                     f"lcp={strategy_result.get('largest_contentful_paint')}"
#                 )
#         else:
#             logger.warning(
#                 "technical_foundation background: completed with error — "
#                 f"website='{result.get('website')}', "
#                 f"message={result.get('message')}"
#             )

#     except Exception as e:
#         logger.exception(
#             f"technical_foundation background: failed — website='{website_url}', error={e}"
#         )


# @router.post(
#     "/technical-foundation",
#     status_code=status.HTTP_202_ACCEPTED
# )
# def technical_foundation(
#     payload: TechnicalFoundationRequest,
#     background_tasks: BackgroundTasks,
# ):
#     logger.info(
#         "technical_foundation route: POST /api/technical-foundation — request received"
#     )

#     background_tasks.add_task(
#         run_technical_foundation,
#         payload.website_url,
#         payload.business_name,
#         payload.business_id,
#     )

#     return {
#         "status": "accepted",
#         "message": "Technical foundation analysis started in background.",
#         "business_name": payload.business_name,
#         "business_id": payload.business_id,
#         "website_url": payload.website_url,
#     }


from fastapi import APIRouter, HTTPException, status

from schema.technical_foundation_schema import TechnicalFoundationRequest
from services.logger_services import logger
from services.technical_foundation import check_technical_foundation

router = APIRouter(
    tags=["Technical Foundation"],
    prefix="/api",
)


@router.post(
    "/technical-foundation",
    status_code=status.HTTP_200_OK
)
def technical_foundation(
    payload: TechnicalFoundationRequest,
):
    logger.info(
        "technical_foundation route: POST /api/technical-foundation — request received"
    )

    try:
        result = check_technical_foundation(
            payload.website_url,
            payload.business_name,
            payload.business_id,
        )

        if result["status"] == "success":
            pagespeed_score = result.get("pagespeed_score", {})
            llms_txt = result.get("llms_txt", {})
            json_ld = result.get("json_ld", {})

            logger.info(
                "technical_foundation completed successfully — "
                f"website='{result.get('website')}', "
                f"pagespeed_score={pagespeed_score.get('score')}/{pagespeed_score.get('max_score')}, "
                f"mobile_score={pagespeed_score.get('mobile', {}).get('score')}, "
                f"desktop_score={pagespeed_score.get('desktop', {}).get('score')}, "
                f"avg_lcp_pts={pagespeed_score.get('lcp_pts')}, "
                f"avg_performance_pts={pagespeed_score.get('performance_pts')}, "
                f"llms_txt_score={llms_txt.get('score')}, "
                f"json_ld_score={json_ld.get('score')}"
            )

            for strategy_result in result.get("results", []):
                logger.info(
                    "technical_foundation strategy result — "
                    f"strategy='{strategy_result.get('strategy')}', "
                    f"performance={strategy_result.get('performance_score')}, "
                    f"lcp={strategy_result.get('largest_contentful_paint')}"
                )

            return result

        logger.warning(
            "technical_foundation completed with error — "
            f"website='{result.get('website')}', "
            f"message={result.get('message')}"
        )

        return result

    except Exception as e:
        logger.exception(
            f"technical_foundation failed — "
            f"website='{payload.website_url}', error={e}"
        )

        raise HTTPException(
            status_code=500,
            detail="Technical foundation analysis failed."
        )