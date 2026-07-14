from services.logger_services import logger
from services.s3_service import load_scraped_result_data

PLATFORMS = ("google_maps", "yelp", "tripadvisor")


def _extract_review(review: dict) -> dict:
    return {
        "author": review.get("author"),
        "rating": review.get("rating"),
        "date": review.get("date"),
        "comment": review.get("comment"),
        "owner_reply": review.get("owner_reply"),
    }


def _empty_error_result(message: str) -> dict:
    return {
        "status": "error",
        "message": message,
        "business": None,
        "scraped_at": None,
        "summary": {
            "total_responses": 0,
            "google_maps": 0,
            "yelp": 0,
            "tripadvisor": 0,
        },
        "all_responses": {
            "google_maps": [],
            "yelp": [],
            "tripadvisor": [],
        },
    }


def get_all_responses(business_name: str) -> dict:
    business_name = business_name.strip()
    logger.info(
        f"all_responses: loading all reviews for business='{business_name}'"
    )

    try:
        scraped_data = load_scraped_result_data(business_name)
    except FileNotFoundError:
        logger.warning(
            f"all_responses: scraped_result.json not found locally or in S3 "
            f"for '{business_name}'"
        )
        return _empty_error_result(
            f"No scraped data found for '{business_name}'. Run scrape API first."
        )

    responses_by_platform: dict[str, list[dict]] = {
        platform: [] for platform in PLATFORMS
    }

    for platform in PLATFORMS:
        reviews = scraped_data.get(platform, {}).get("reviews", [])
        for review in reviews:
            responses_by_platform[platform].append(_extract_review(review))

        logger.info(
            f"all_responses: [{platform}] "
            f"{len(responses_by_platform[platform])} reviews"
        )

    summary = {
        "total_responses": sum(
            len(reviews) for reviews in responses_by_platform.values()
        ),
        "google_maps": len(responses_by_platform["google_maps"]),
        "yelp": len(responses_by_platform["yelp"]),
        "tripadvisor": len(responses_by_platform["tripadvisor"]),
    }

    logger.info(
        f"all_responses: completed — business='{scraped_data.get('business')}', "
        f"total_responses={summary['total_responses']}"
    )

    return {
        "status": "success",
        "business": scraped_data.get("business"),
        "scraped_at": scraped_data.get("scraped_at"),
        "summary": summary,
        "all_responses": responses_by_platform,
    }
