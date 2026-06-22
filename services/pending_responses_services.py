import json
from pathlib import Path

from services.logger_services import logger

SCRAPED_RESULT_PATH = Path(__file__).resolve().parent.parent / "scraped_result.json"

PLATFORMS = ("google_maps", "yelp", "tripadvisor")


def _is_pending(owner_reply) -> bool:
    if owner_reply is None:
        return True
    return not str(owner_reply).strip()


def _extract_pending_review(review: dict) -> dict:
    return {
        "author": review.get("author"),
        "rating": review.get("rating"),
        "date": review.get("date"),
        "comment": review.get("comment"),
    }


def get_pending_responses() -> dict:
    logger.info("pending_responses: loading pending reviews from scraped_result.json")

    if not SCRAPED_RESULT_PATH.exists():
        logger.warning(
            f"pending_responses: scraped_result.json not found at {SCRAPED_RESULT_PATH}"
        )
        return {
            "status": "error",
            "message": "No scraped_result.json found. Run scrape API first.",
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

    with open(SCRAPED_RESULT_PATH, "r", encoding="utf-8") as file:
        scraped_data = json.load(file)

    pending_by_platform: dict[str, list[dict]] = {
        platform: [] for platform in PLATFORMS
    }

    for platform in PLATFORMS:
        reviews = scraped_data.get(platform, {}).get("reviews", [])
        for review in reviews:
            if _is_pending(review.get("owner_reply")):
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
        "business": scraped_data.get("business"),
        "scraped_at": scraped_data.get("scraped_at"),
        "summary": summary,
        "pending_responses": pending_by_platform,
    }
