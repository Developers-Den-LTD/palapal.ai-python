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


def _has_comment(comment) -> bool:
    if comment is None:
        return False
    return bool(str(comment).strip())


def _to_float_rating(rating):
    if rating is None:
        return None
    try:
        return float(rating)
    except (TypeError, ValueError):
        return None


def _round_avg(value):
    if value is None:
        return None
    return round(value, 2)


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
            "avg_rating_overall": None,
            "avg_rating_google_maps": None,
            "avg_rating_yelp": None,
            "avg_rating_tripadvisor": None,
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
    rating_totals: dict[str, dict[str, float | int]] = {
        platform: {"sum": 0.0, "count": 0} for platform in PLATFORMS
    }

    for platform in PLATFORMS:
        reviews = scraped_data.get(platform, {}).get("reviews", [])
        for review in reviews:
            if not _has_comment(review.get("comment")):
                continue
            responses_by_platform[platform].append(_extract_review(review))
            rating_value = _to_float_rating(review.get("rating"))
            if rating_value is not None:
                rating_totals[platform]["sum"] += rating_value
                rating_totals[platform]["count"] += 1

        logger.info(
            f"all_responses: [{platform}] "
            f"{len(responses_by_platform[platform])} reviews"
        )

    avg_by_platform = {}
    for platform in PLATFORMS:
        count = rating_totals[platform]["count"]
        total = rating_totals[platform]["sum"]
        avg_by_platform[platform] = _round_avg(total / count) if count else None

    total_rating_sum = sum(rating_totals[platform]["sum"] for platform in PLATFORMS)
    total_rating_count = sum(rating_totals[platform]["count"] for platform in PLATFORMS)
    avg_rating_overall = _round_avg(
        total_rating_sum / total_rating_count if total_rating_count else None
    )

    summary = {
        "total_responses": sum(
            len(reviews) for reviews in responses_by_platform.values()
        ),
        "google_maps": len(responses_by_platform["google_maps"]),
        "yelp": len(responses_by_platform["yelp"]),
        "tripadvisor": len(responses_by_platform["tripadvisor"]),
        "avg_rating_overall": avg_rating_overall,
        "avg_rating_google_maps": avg_by_platform["google_maps"],
        "avg_rating_yelp": avg_by_platform["yelp"],
        "avg_rating_tripadvisor": avg_by_platform["tripadvisor"],
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
