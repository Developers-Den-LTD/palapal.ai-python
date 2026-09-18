from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from schema.review_extension_platforms_schema import ReviewExtensionPlatformsRequest
from services.logger_services import logger
from services.review_extension_platforms_services import scrape_review_extension_platforms
from services.webhook_poster import post_to_webhook

router = APIRouter(tags=["Review Extension Platforms API"])


def _failed_platform_block(url_key: str, url_value, error: Exception) -> dict:
    return {
        "status": f"❌ Failed: {str(error)}",
        url_key: str(url_value) if url_value else None,
        "total_reviews": 0,
        "reviews": [],
    }


async def _run_and_notify(payload: ReviewExtensionPlatformsRequest) -> None:
    webhook_url = str(payload.webhook_url)
    try:
        result = await scrape_review_extension_platforms(payload)
        await post_to_webhook(webhook_url, result)
    except Exception as e:
        logger.exception(
            f"review_extension_platforms route: background failed — {e}"
        )
        try:
            await post_to_webhook(
                webhook_url,
                {
                    "business_name": payload.business_name,
                    "business_id": payload.business_id,
                    "summary": {
                        "facebook": f"❌ Failed: {str(e)}",
                        "trustpilot": f"❌ Failed: {str(e)}",
                        "feefo": f"❌ Failed: {str(e)}",
                    },
                    "facebook": _failed_platform_block(
                        "facebook_url", payload.facebook_url, e
                    ),
                    "trustpilot": _failed_platform_block(
                        "trustpilot_url", payload.trustpilot_url, e
                    ),
                    "feefo": _failed_platform_block(
                        "feefo_url", payload.feefo_url, e
                    ),
                },
            )
        except Exception as webhook_error:
            logger.exception(
                "review_extension_platforms route: failed to post error "
                f"to webhook — {webhook_error}"
            )


@router.post(
    "/api/review_extension_platforms",
    status_code=status.HTTP_200_OK,
)
def review_extension_platforms(
    payload: ReviewExtensionPlatformsRequest,
    background_tasks: BackgroundTasks,
):
    logger.info(
        "review_extension_platforms route: POST /api/review_extension_platforms — "
        f"business_name='{payload.business_name}', "
        f"business_id='{payload.business_id}', "
        f"facebook_url='{payload.facebook_url}', "
        f"trustpilot_url='{payload.trustpilot_url}', "
        f"feefo_url='{payload.feefo_url}', "
        f"max_reviews={payload.max_reviews}, "
        f"webhook_url='{payload.webhook_url}'"
    )

    if not payload.webhook_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook_url is required",
        )

    background_tasks.add_task(_run_and_notify, payload)
    return {
        "status": "accepted",
        "message": "reviews scraping is being processed in the background.",
    }
