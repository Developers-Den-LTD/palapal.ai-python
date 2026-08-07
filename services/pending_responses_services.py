from services.logger_services import logger
from services.s3_service import load_scraped_result_data
from utils.scraped_result_paths import build_scrape_storage_slug

PLATFORMS = ("google_maps", "yelp", "tripadvisor")


def _is_pending(owner_reply, comment) -> bool:
    if comment is None or not str(comment).strip():
        return False
    if owner_reply is None:
        return True
    return not str(owner_reply).strip()


def _extract_pending_review(review: dict) -> dict:
    return {
        "UUID": review.get("UUID"),
        "author": review.get("author"),
        "rating": review.get("rating"),
        "date": review.get("date"),
        "comment": review.get("comment"),
        "owner_reply": review.get("owner_reply"),
        "AI_Draft": review.get("AI_Draft"),
    }


def _empty_error_result(
    message: str,
    *,
    business_name: str = "",
    business_id: str | int | None = None,
    storage_slug: str = "",
) -> dict:
    return {
        "status": "error",
        "message": message,
        "business_name": business_name,
        "business_id": business_id,
        "storage_slug": storage_slug,
        "business": None,
        "scraped_at": None,
        "summary": {
            "total_pending": 0,
            "google_maps": 0,
            "yelp": 0,
            "tripadvisor": 0,
        },
        "pending_responses": {
            "google_maps": [],
            "yelp": [],
            "tripadvisor": [],
        },
    }


def get_pending_responses(
    business_name: str,
    business_id: str | int | None = None,
) -> dict:
    business_name = business_name.strip()
    storage_slug = build_scrape_storage_slug(business_name, business_id)
    logger.info(
        f"pending_responses: loading pending reviews for business='{business_name}', "
        f"business_id='{business_id}', storage_slug='{storage_slug}'"
    )

    try:
        scraped_data = load_scraped_result_data(business_name, business_id)
    except FileNotFoundError:
        logger.warning(
            f"pending_responses: scraped_result.json not found locally or in S3 "
            f"for '{business_name}' (slug='{storage_slug}')"
        )
        return _empty_error_result(
            f"No scraped data found for '{business_name}'"
            f"{f' with business_id={business_id}' if business_id is not None else ''}. "
            "Run scrape API first.",
            business_name=business_name,
            business_id=business_id,
            storage_slug=storage_slug,
        )

    pending_by_platform: dict[str, list[dict]] = {
        platform: [] for platform in PLATFORMS
    }

    for platform in PLATFORMS:
        reviews = scraped_data.get(platform, {}).get("reviews", [])
        for review in reviews:
            if _is_pending(review.get("owner_reply"), review.get("comment")):
                pending_by_platform[platform].append(_extract_pending_review(review))

        logger.info(
            f"pending_responses: [{platform}] "
            f"{len(pending_by_platform[platform])} pending of {len(reviews)} reviews"
        )

    summary = {
        "total_pending": sum(len(reviews) for reviews in pending_by_platform.values()),
        "google_maps": len(pending_by_platform["google_maps"]),
        "yelp": len(pending_by_platform["yelp"]),
        "tripadvisor": len(pending_by_platform["tripadvisor"]),
    }

    logger.info(
        f"pending_responses: completed — business='{scraped_data.get('business')}', "
        f"total_pending={summary['total_pending']}"
    )

    return {
        "status": "success",
        "business_name": business_name,
        "business_id": business_id,
        "storage_slug": storage_slug,
        "business": scraped_data.get("business"),
        "scraped_at": scraped_data.get("scraped_at"),
        "summary": summary,
        "pending_responses": pending_by_platform,
    }
