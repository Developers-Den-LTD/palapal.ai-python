"""
API route for llms.txt generation.

POST /api/generate-llms-txt

Two modes:
  - No webhook_url: wait for full result and return it in the response (sync).
  - With webhook_url: return 202 immediately, run in background, POST result to webhook.
"""

import json

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse

from schema.llms_txt_generator_schema import (
    LlmsTxtCrawlRequest,
    LlmsTxtFromUrlsRequest,
    LlmsTxtGeneratorRequest,
)
from services.llms_txt_crawler_services import crawl_website_urls
from services.llms_txt_generator_services import (
    generate_llms_txt,
    generate_llms_txt_from_urls,
)
from services.logger_services import logger
from services.webhook_poster import post_to_webhook

router = APIRouter(
    tags=["LLMS.txt Generator"],
    prefix="/api",
)


async def _run_crawl_website_urls_and_notify(payload: LlmsTxtCrawlRequest) -> None:
    """Background job: crawl website URLs and POST the result (or error) to webhook."""
    webhook_url = str(payload.webhook_url)
    try:
        result = crawl_website_urls(
            payload.website_url,
            max_pages=payload.max_pages,
            delay_seconds=payload.delay_seconds,
            max_workers=payload.max_workers,
            prefer_sitemap=payload.prefer_sitemap,
        )
        logger.info(
            "llms_txt_generator background crawl: completed — "
            f"website='{result['website_url']}', "
            f"url_count={result['url_count']}"
        )
        logger.info(
            "llms_txt_generator background crawl: webhook payload — "
            f"url='{webhook_url}', "
            f"body={json.dumps(result, ensure_ascii=False, default=str)}"
        )
        await post_to_webhook(webhook_url, result)
    except Exception as exc:
        logger.exception(f"llms_txt_generator background crawl: failed — {exc}")
        error_payload = {
            "status": "error",
            "message": str(exc),
            "website_url": payload.website_url,
        }
        logger.info(
            "llms_txt_generator background crawl: webhook payload — "
            f"url='{webhook_url}', "
            f"body={json.dumps(error_payload, ensure_ascii=False, default=str)}"
        )
        await post_to_webhook(webhook_url, error_payload)


async def _run_llms_txt_generation_and_notify(payload: LlmsTxtGeneratorRequest) -> None:
    """
    Background job: run the full pipeline and send the result (or error) to webhook.
    """
    try:
        result = await generate_llms_txt(payload)
        logger.info(
            "llms_txt_generator background: completed — "
            f"website='{result['website_url']}', "
            f"business='{result['business_name']}', "
            f"chars={len(result['llms_txt_content'])}"
        )
        if payload.webhook_url:
            await post_to_webhook(str(payload.webhook_url), result)
    except Exception as exc:
        logger.exception(f"llms_txt_generator background: failed — {exc}")
        if payload.webhook_url:
            await post_to_webhook(
                str(payload.webhook_url),
                {
                    "status": "error",
                    "message": str(exc),
                    "website_url": payload.website_url,
                    "business_name": payload.business_name,
                    "business_id": payload.business_id,
                },
            )


@router.post("/crawl-website-urls")
def crawl_website_urls_route(
    payload: LlmsTxtCrawlRequest,
    background_tasks: BackgroundTasks,
):
    """
    Crawl a website in the background and POST internal page URLs to webhook_url.

    Login, signup, cart, legal, and other low-value URLs are excluded automatically.
    Body: website_url (required), webhook_url (required), optional crawl settings.
    Header: X-API-KEY required (set in application.py).
    """
    logger.info(
        "llms_txt_generator route: POST /api/crawl-website-urls — "
        f"website_url='{payload.website_url}', "
        f"webhook_url='{payload.webhook_url}', "
        f"max_pages={payload.max_pages}, "
        f"delay_seconds={payload.delay_seconds}, "
        f"max_workers={payload.max_workers}, "
        f"prefer_sitemap={payload.prefer_sitemap}"
    )

    if not payload.webhook_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook_url is required",
        )

    background_tasks.add_task(_run_crawl_website_urls_and_notify, payload)
    logger.info(
        "llms_txt_generator route: accepted — background crawl queued for webhook "
        f"{payload.webhook_url}"
    )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "message": "Website crawl started. Results will be sent to the webhook URL.",
            "website_url": payload.website_url,
            "webhook_url": str(payload.webhook_url),
        },
    )


@router.post("/generate-llms-txt-from-urls")
async def generate_llms_txt_from_urls_route(payload: LlmsTxtFromUrlsRequest):
    """
    Extract title, headings, description, content, and business info from selected URLs
    using 8 async workers, then generate llms.txt content with OpenAI (gpt-4o-mini).

    Body: website_url, urls (required), optional business_name and max_workers.
    Header: X-API-KEY required (set in application.py).
    """
    logger.info(
        "llms_txt_generator route: POST /api/generate-llms-txt-from-urls — "
        f"website_url='{payload.website_url}', "
        f"url_count={len(payload.urls)}, "
        f"max_workers={payload.max_workers}"
    )
    try:
        return await generate_llms_txt_from_urls(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception(
            f"llms_txt_generator route: generate from URLs failed — {exc}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"llms.txt generation failed: {str(exc)}",
        )


@router.post("/generate-llms-txt")
async def generate_llms_txt_route(
    payload: LlmsTxtGeneratorRequest,
    background_tasks: BackgroundTasks,
):
    """
    Entry point for clients.

    Body: website_url (required), optional business_name, business_id, webhook_url.
    Header: X-API-KEY required (set in application.py).
    """
    logger.info(
        "llms_txt_generator route: POST /api/generate-llms-txt — "
        f"website_url='{payload.website_url}', "
        f"business_name='{payload.business_name}', "
        f"business_id='{payload.business_id}', "
        f"webhook_url='{payload.webhook_url}'"
    )

    # Webhook mode: do not block the client — process in background.
    if payload.webhook_url:
        background_tasks.add_task(_run_llms_txt_generation_and_notify, payload)
        logger.info(
            "llms_txt_generator route: accepted — background job queued for webhook "
            f"{payload.webhook_url}"
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "status": "accepted",
                "message": (
                    "llms.txt generation started. Results will be sent to the webhook URL."
                ),
                "website_url": payload.website_url,
                "business_name": payload.business_name,
                "business_id": payload.business_id,
                "webhook_url": str(payload.webhook_url),
            },
        )

    # Sync mode: run pipeline now and return llms_txt_content in the response.
    try:
        result = await generate_llms_txt(payload)
        return result
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception(f"llms_txt_generator route: request failed — {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"llms.txt generation failed: {str(exc)}",
        )
