import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse

from schema.ai_keyword_generation_schema import AIKeywordGenerationRequest
from services.ai_keyword_generation_services import run_ai_keyword_generation
from services.logger_services import logger
from services.webhook_poster import post_to_webhook

router = APIRouter(
    tags=["AI Keyword Generation"],
    prefix="/api",
)


async def _run_ai_keyword_generation_and_notify(
    payload: AIKeywordGenerationRequest,
) -> None:
    webhook_url = str(payload.webhook_url)
    try:
        result = await asyncio.to_thread(run_ai_keyword_generation, payload)
        logger.info(
            "ai_keyword_generation background: completed — "
            f"status={result['status']}, "
            f"total_keywords={result.get('total_keywords')}, "
            f"provider='{result.get('provider')}', "
            f"model='{result.get('model')}'"
        )
        await post_to_webhook(webhook_url, result)
    except Exception as e:
        logger.exception(f"ai_keyword_generation background: failed — {e}")
        await post_to_webhook(
            webhook_url,
            {
                "status": "error",
                "message": str(e),
                "business_name": payload.business_name,
                "business_id": payload.business_id,
                "industry": payload.industry,
                "services": payload.services,
                "location": payload.location,
                "website_url": payload.website_url,
            },
        )


@router.post("/ai-keyword-generation")
async def ai_keyword_generation(
    payload: AIKeywordGenerationRequest,
    background_tasks: BackgroundTasks,
):
    logger.info(
        "ai_keyword_generation route: POST /api/ai-keyword-generation — "
        f"business='{payload.business_name}', "
        f"business_id='{payload.business_id}', "
        f"industry='{payload.industry}', "
        f"location='{payload.location}', "
        f"services={len(payload.services)}, "
        f"website='{payload.website_url}', "
        f"has_existing_content={bool(payload.existing_content)}, "
        f"webhook_url='{payload.webhook_url}'"
    )

    if not payload.webhook_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook_url is required",
        )

    background_tasks.add_task(_run_ai_keyword_generation_and_notify, payload)
    logger.info(
        "ai_keyword_generation route: accepted — background job queued for webhook "
        f"{payload.webhook_url}"
    )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "message": (
                "AI keyword generation started. "
                "Results will be sent to the webhook URL."
            ),
            "webhook_url": str(payload.webhook_url),
            "business_name": payload.business_name,
            "business_id": payload.business_id,
            "industry": payload.industry,
            "location": payload.location,
            "website_url": payload.website_url,
            "service_count": len(payload.services),
        },
    )
