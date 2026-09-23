from services.logger_services import logger
from services.s3_service import load_scraped_result_data
from utils.scraped_result_paths import build_scrape_storage_slug

PLATFORMS = (
    "google_maps",
    "yelp",
    "tripadvisor",
    "facebook",
    "trustpilot",
    "feefo",
)


def _first_present(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _review_comment(review: dict):
    """
    Core scrapers store review text as `comment`.
    Extension platforms (facebook/trustpilot/feefo) store it as `text`.
    Prefer a non-empty comment when both exist.
    """
    comment = review.get("comment")
    if comment is not None and str(comment).strip():
        return comment
    text = review.get("text")
    if text is not None and str(text).strip():
        return text
    return _first_present(comment, text)


def _review_author(review: dict):
    return _first_present(review.get("author"), review.get("authorName"))


def _review_date(review: dict):
    return _first_present(review.get("date"), review.get("publishedDate"))


def _extract_review(review: dict) -> dict:
    # Same response shape as google_maps / yelp / tripadvisor.
    return {
        "UUID": review.get("UUID"),
        "template_id": review.get("template_id"),
        "author": _review_author(review),
        "rating": review.get("rating"),
        "date": _review_date(review),
        "comment": _review_comment(review),
        "owner_reply": review.get("owner_reply"),
        "AI_Draft": review.get("AI_Draft"),
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
            "total_responses": 0,
            "google_maps": 0,
            "yelp": 0,
            "tripadvisor": 0,
            "facebook": 0,
            "trustpilot": 0,
            "feefo": 0,
            "avg_rating_overall": None,
            "avg_rating_google_maps": None,
            "avg_rating_yelp": None,
            "avg_rating_tripadvisor": None,
            "avg_rating_facebook": None,
            "avg_rating_trustpilot": None,
            "avg_rating_feefo": None,
        },
        "all_responses": {
            "google_maps": [],
            "yelp": [],
            "tripadvisor": [],
            "facebook": [],
            "trustpilot": [],
            "feefo": [],
        },
    }


def get_all_responses(
    business_name: str,
    business_id: str | int | None = None,
) -> dict:
    business_name = business_name.strip()
    storage_slug = build_scrape_storage_slug(business_name, business_id)
    logger.info(
        f"all_responses: loading all reviews for business='{business_name}', "
        f"business_id='{business_id}', storage_slug='{storage_slug}'"
    )

    try:
        scraped_data = load_scraped_result_data(business_name, business_id)
    except FileNotFoundError:
        logger.warning(
            f"all_responses: scraped_result.json not found locally or in S3 "
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

    responses_by_platform: dict[str, list[dict]] = {
        platform: [] for platform in PLATFORMS
    }
    rating_totals: dict[str, dict[str, float | int]] = {
        platform: {"sum": 0.0, "count": 0} for platform in PLATFORMS
    }

    for platform in PLATFORMS:
        reviews = scraped_data.get(platform, {}).get("reviews", [])
        for review in reviews:
            if not isinstance(review, dict):
                continue
            comment = _review_comment(review)
            if not _has_comment(comment):
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
        "facebook": len(responses_by_platform["facebook"]),
        "trustpilot": len(responses_by_platform["trustpilot"]),
        "feefo": len(responses_by_platform["feefo"]),
        "avg_rating_overall": avg_rating_overall,
        "avg_rating_google_maps": avg_by_platform["google_maps"],
        "avg_rating_yelp": avg_by_platform["yelp"],
        "avg_rating_tripadvisor": avg_by_platform["tripadvisor"],
        "avg_rating_facebook": avg_by_platform["facebook"],
        "avg_rating_trustpilot": avg_by_platform["trustpilot"],
        "avg_rating_feefo": avg_by_platform["feefo"],
    }

    logger.info(
        f"all_responses: completed — business='{scraped_data.get('business')}', "
        f"total_responses={summary['total_responses']}"
    )

    return {
        "status": "success",
        "business_name": business_name,
        "business_id": business_id,
        "storage_slug": storage_slug,
        "business": scraped_data.get("business"),
        "scraped_at": scraped_data.get("scraped_at"),
        "summary": summary,
        "all_responses": responses_by_platform,
    }
