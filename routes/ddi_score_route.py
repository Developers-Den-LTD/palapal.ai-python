from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from schema.ddi_score_schema import DDIScoreRequest
from services.ddi_score_services import calculate_ddi_score
from services.logger_services import logger
from services.webhook_poster import post_to_webhook

router = APIRouter(
    tags=["DDI Score"],
    prefix="/api",
)


async def _run_ddi_score_and_notify(payload: DDIScoreRequest) -> None:
    webhook_url = str(payload.webhook_url)
    try:
        result = await calculate_ddi_score(payload)
        logger.info(
            f"ddi_score background: completed — status={result['status']}, "
            f"DDI_final_score={result['DDI_final_score']}/{result['max_DDI_final_score']}"
        )
        await post_to_webhook(webhook_url, result)
    except Exception as e:
        logger.exception(f"ddi_score background: failed — {e}")
        await post_to_webhook(
            webhook_url,
            {
                "status": "error",
                "message": str(e),
                "business_name": payload.business_name,
                "business_type": payload.business_type,
                "business_location": payload.business_loc,
                "website_url": payload.website_url,
            },
        )


@router.post(
    "/ddi-score"
)
async def ddi_score(
    payload: DDIScoreRequest,
    background_tasks: BackgroundTasks,
):
    logger.info(
        f"ddi_score route: POST /api/ddi-score — "
        f"business='{payload.business_name}', "
        f"type='{payload.business_type}', loc='{payload.business_loc}', "
        f"website='{payload.website_url}', "
        f"webhook='{payload.webhook_url}'"
    )

    if not payload.webhook_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook_url is required",
        )

    background_tasks.add_task(_run_ddi_score_and_notify, payload)
    logger.info(
        f"ddi_score route: accepted — background job queued for webhook "
        f"{payload.webhook_url}"
    )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "message": "DDI score calculation started. Results will be sent to the webhook URL.",
            "webhook_url": str(payload.webhook_url),
            "business_name": payload.business_name,
            "business_type": payload.business_type,
            "business_location": payload.business_loc,
            "website_url": payload.website_url,
        },
    )
