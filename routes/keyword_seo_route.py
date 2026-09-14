import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse

from schema.keyword_seo_schema import KeywordSeoRequest
from services.keyword_seo_services import run_keyword_seo_analysis
from services.logger_services import logger
from services.webhook_poster import post_to_webhook

router = APIRouter(
    tags=["Keyword SEO Analysis"],
    prefix="/api",
)


async def _run_keyword_seo_and_notify(payload: KeywordSeoRequest) -> None:
    """
    Run Apify SEO work off the event loop so concurrent API requests
    can still be accepted while a job is in progress.
    """
    webhook_url = str(payload.webhook_url)
    try:
        result = await asyncio.to_thread(run_keyword_seo_analysis, payload)
        matched = result.get("matched_keyword") or {}
        logger.info(
            "keyword_seo background: completed — "
            f"status={result['status']}, found={result.get('found')}, "
            f"matched_keyword='{matched.get('keyword')}', "
            f"priority='{matched.get('priority')}', "
            f"position={matched.get('position')}, "
            f"keywords_tried={len(result.get('attempts') or [])}, "
            f"total_actor_runs={result.get('total_actor_runs')}"
        )
        await post_to_webhook(webhook_url, result)
    except Exception as e:
        logger.exception(f"keyword_seo background: failed — {e}")
        await post_to_webhook(
            webhook_url,
            {
                "status": "error",
                "message": str(e),
                "website_url": payload.website_url,
                "country_code": payload.country_code,
            },
        )


@router.post("/keyword-seo-analysis")
async def keyword_seo_analysis(
    payload: KeywordSeoRequest,
    background_tasks: BackgroundTasks,
):
    logger.info(
        "keyword_seo route: POST /api/keyword-seo-analysis — "
        f"website='{payload.website_url}', "
        f"country_code='{payload.country_code}', "
        f"keywords={len(payload.keywords)}, "
        f"webhook_url='{payload.webhook_url}'"
    )

    if not payload.webhook_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook_url is required",
        )

    background_tasks.add_task(_run_keyword_seo_and_notify, payload)
    logger.info(
        "keyword_seo route: accepted — background job queued for webhook "
        f"{payload.webhook_url}"
    )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "message": (
                "Keyword SEO analysis started. "
                "Results will be sent to the webhook URL."
            ),
            "webhook_url": str(payload.webhook_url),
            "website_url": payload.website_url,
            "country_code": payload.country_code,
            "keyword_count": len(payload.keywords),
        },
    )
