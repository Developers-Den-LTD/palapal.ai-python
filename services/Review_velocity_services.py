import json
from datetime import datetime, timedelta
from pathlib import Path

from services.logger_services import logger
from utils.scraped_result_paths import get_scraped_result_path, slugify_folder_name

PLATFORM_MAP = {
    "google_maps": "google",
    "yelp": "yelp",
    "tripadvisor": "tripadvisor",
}

MAX_REVIEW_VELOCITY_SCORE = 15
MAX_STAR_RATING_DECAY_SCORE = 15
MAX_RESPONSE_RATE_SCORE = 10
MAX_DDI_REPUTATION_SCORE = (
    MAX_REVIEW_VELOCITY_SCORE + MAX_STAR_RATING_DECAY_SCORE + MAX_RESPONSE_RATE_SCORE
)

DATE_FORMAT = "%d %B %Y"
SCRAPED_AT_FORMAT = "%d %B %Y %H:%M"


def _log_section(title: str) -> None:
    logger.info(f"review_velocity: {'=' * 60}")
    logger.info(f"review_velocity: {title}")
    logger.info(f"review_velocity: {'=' * 60}")


def _parse_review_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), DATE_FORMAT)
    except ValueError:
        logger.warning(f"review_velocity: could not parse date '{date_str}'")
        return None


def _parse_reference_date(scraped_data: dict) -> datetime:
    scraped_at = scraped_data.get("scraped_at", "")
    try:
        return datetime.strptime(scraped_at.strip(), SCRAPED_AT_FORMAT)
    except ValueError:
        logger.warning(
            f"review_velocity: could not parse scraped_at '{scraped_at}', using current date"
        )
        return datetime.now()


def _is_replied(owner_reply) -> bool:
    if owner_reply is None:
        return False
    return bool(str(owner_reply).strip())


def _load_reviews(scraped_data: dict) -> list[dict]:
    _log_section("Step 1 — Loading reviews from scraping_results")
    reviews = []

    for platform_key, platform_name in PLATFORM_MAP.items():
        platform_reviews = scraped_data.get(platform_key, {}).get("reviews", [])
        logger.info(
            f"review_velocity: [{platform_name}] loaded {len(platform_reviews)} reviews"
        )
        print(f"[{platform_name}] loaded {len(platform_reviews)} reviews")

        for review in platform_reviews:
            reviews.append(
                {
                    "platform": platform_name,
                    "rating": review.get("rating"),
                    "date": review.get("date"),
                    "comment": review.get("comment"),
                    "owner_reply": review.get("owner_reply"),
                }
            )

    logger.info(f"review_velocity: total reviews loaded = {len(reviews)}")
    print(f"\nTotal reviews loaded: {len(reviews)}\n")
    return reviews


def _calculate_review_velocity(reviews: list[dict], reference_date: datetime) -> dict:
    _log_section("Step 2 — Review Velocity (last 30 days)")
    cutoff = reference_date - timedelta(days=30)
    logger.info(
        f"review_velocity: reference_date={reference_date.strftime(DATE_FORMAT)}, "
        f"cutoff_date={cutoff.strftime(DATE_FORMAT)}"
    )
    print(
        f"Reference date : {reference_date.strftime(DATE_FORMAT)}\n"
        f"Cutoff (30 days): {cutoff.strftime(DATE_FORMAT)}\n"
    )

    recent_reviews = []

    for review in reviews:
        review_date = _parse_review_date(review.get("date"))
        if review_date and review_date >= cutoff:
            recent_reviews.append(review)
            logger.info(
                f"review_velocity: [velocity] [{review['platform']}] "
                f"rating={review.get('rating')}, date={review.get('date')}"
            )
            print(
                f"  [{review['platform']}] {review.get('rating')} stars — {review.get('date')}"
            )

    count = len(recent_reviews)

    if count >= 30:
        score = 15
        label = "Excellent volume"
    elif count >= 15:
        score = 10
        label = "Good volume"
    elif count >= 5:
        score = 5
        label = "Low volume"
    else:
        score = 0
        label = "Stale profile"

    logger.info(f"review_velocity: reviews_last_30_days = {count}")
    logger.info(f"review_velocity: velocity score = {score}/{MAX_REVIEW_VELOCITY_SCORE} ({label})")
    print(f"\nReviews in last 30 days: {count}")
    print(f"Velocity score       : {score}/{MAX_REVIEW_VELOCITY_SCORE} ({label})\n")

    return {
        "reviews_last_30_days": count,
        "score": score,
        "max_score": MAX_REVIEW_VELOCITY_SCORE,
        "label": label,
        "recent_reviews": recent_reviews,
    }


def _age_weight(days_old: int) -> float:
    if days_old <= 30:
        return 1.0
    age_months = days_old / 30.0
    return 1.0 / (1.0 + age_months)


def _calculate_star_rating_decay(reviews: list[dict], reference_date: datetime) -> dict:
    _log_section("Step 3 — Star Rating Time-Decay")
    weighted_sum = 0.0
    total_weight = 0.0
    used_reviews = 0

    for review in reviews:
        rating = review.get("rating")
        review_date = _parse_review_date(review.get("date"))

        if rating is None or review_date is None:
            logger.warning(
                f"review_velocity: [decay] skipped review — "
                f"platform={review.get('platform')}, rating={rating}, date={review.get('date')}"
            )
            continue

        days_old = max(0, (reference_date - review_date).days)
        weight = _age_weight(days_old)
        weighted_sum += float(rating) * weight
        total_weight += weight
        used_reviews += 1

        logger.info(
            f"review_velocity: [decay] [{review['platform']}] "
            f"rating={rating}, date={review.get('date')}, "
            f"days_old={days_old}, weight={round(weight, 4)}"
        )

    if total_weight == 0:
        weighted_avg = 0.0
        score = 0
    else:
        weighted_avg = weighted_sum / total_weight
        score = round((weighted_avg / 5.0) * MAX_STAR_RATING_DECAY_SCORE, 2)

    logger.info(f"review_velocity: weighted_average_rating = {round(weighted_avg, 2)}")
    logger.info(f"review_velocity: total_weighted_reviews = {used_reviews}")
    logger.info(
        f"review_velocity: decay score = {score}/{MAX_STAR_RATING_DECAY_SCORE}"
    )

    print(f"Weighted average rating : {round(weighted_avg, 2)}")
    print(f"Reviews used in decay  : {used_reviews}")
    print(f"Decay score            : {score}/{MAX_STAR_RATING_DECAY_SCORE}\n")

    return {
        "weighted_average_rating": round(weighted_avg, 2),
        "total_weighted_reviews": used_reviews,
        "score": score,
        "max_score": MAX_STAR_RATING_DECAY_SCORE,
    }


def _calculate_response_rate(reviews: list[dict], reference_date: datetime) -> dict:
    _log_section("Step 4 — Response Rate (last 90 days)")
    cutoff = reference_date - timedelta(days=90)
    logger.info(
        f"review_velocity: reference_date={reference_date.strftime(DATE_FORMAT)}, "
        f"cutoff_date={cutoff.strftime(DATE_FORMAT)}"
    )
    print(f"Cutoff (90 days): {cutoff.strftime(DATE_FORMAT)}\n")

    recent_reviews = []

    for review in reviews:
        review_date = _parse_review_date(review.get("date"))
        if review_date and review_date >= cutoff:
            recent_reviews.append(review)
            replied = _is_replied(review.get("owner_reply"))
            logger.info(
                f"review_velocity: [response] [{review['platform']}] "
                f"date={review.get('date')}, is_replied={replied}"
            )

    total = len(recent_reviews)
    replied = sum(1 for review in recent_reviews if _is_replied(review.get("owner_reply")))
    response_rate = round((replied / total) * 100, 2) if total else 0.0

    if response_rate >= 80:
        score = 10
        label = ">= 80% response rate"
    elif response_rate >= 50:
        score = 6
        label = "50% to 79% response rate"
    elif response_rate >= 20:
        score = 3
        label = "20% to 49% response rate"
    else:
        score = 0
        label = "< 20% response rate"

    logger.info(f"review_velocity: reviews_last_90_days = {total}")
    logger.info(f"review_velocity: replied_reviews = {replied}")
    logger.info(f"review_velocity: response_rate_pct = {response_rate}%")
    logger.info(
        f"review_velocity: response score = {score}/{MAX_RESPONSE_RATE_SCORE} ({label})"
    )

    print(f"Reviews in last 90 days : {total}")
    print(f"Owner replies           : {replied}")
    print(f"Response rate           : {response_rate}%")
    print(f"Response score          : {score}/{MAX_RESPONSE_RATE_SCORE} ({label})\n")

    return {
        "reviews_last_90_days": total,
        "replied_reviews": replied,
        "response_rate_pct": response_rate,
        "score": score,
        "max_score": MAX_RESPONSE_RATE_SCORE,
        "label": label,
    }


def _error_result(message: str) -> dict:
    return {
        "status": "error",
        "message": message,
        "reviews": [],
        "review_velocity": {
            "reviews_last_30_days": 0,
            "score": 0,
            "max_score": MAX_REVIEW_VELOCITY_SCORE,
            "label": "Stale profile",
            "recent_reviews": [],
        },
        "star_rating_decay": {
            "weighted_average_rating": 0.0,
            "total_weighted_reviews": 0,
            "score": 0,
            "max_score": MAX_STAR_RATING_DECAY_SCORE,
        },
        "response_rate": {
            "reviews_last_90_days": 0,
            "replied_reviews": 0,
            "response_rate_pct": 0.0,
            "score": 0,
            "max_score": MAX_RESPONSE_RATE_SCORE,
            "label": "< 20% response rate",
        },
        "DDI_Reputation_Score_Result": 0,
        "max_DDI_Reputation_Score": MAX_DDI_REPUTATION_SCORE,
    }


def analyze_reputation_score(business_name: str) -> dict:
    _log_section("Reputation Score analysis started")

    business_name = business_name.strip()
    folder_slug = slugify_folder_name(business_name)
    scraped_result_path = get_scraped_result_path(business_name)

    logger.info(
        f"review_velocity: business_name='{business_name}', folder_slug='{folder_slug}'"
    )
    logger.info(f"review_velocity: looking for scraped data at {scraped_result_path}")

    if not scraped_result_path.exists():
        logger.warning(
            f"review_velocity: scraped_result.json not found at {scraped_result_path}"
        )
        return _error_result(
            f"No scraped data found for '{business_name}'. "
            f"Expected file at scraping_results/{folder_slug}/scraped_result.json. "
            "Run scrape API first."
        )

    with open(scraped_result_path, "r", encoding="utf-8") as file:
        scraped_data = json.load(file)

    business = scraped_data.get("business", business_name)
    reference_date = _parse_reference_date(scraped_data)

    logger.info(f"review_velocity: business='{business}'")
    logger.info(f"review_velocity: scraped_at='{scraped_data.get('scraped_at')}'")
    logger.info(
        f"review_velocity: reference_date='{reference_date.strftime(DATE_FORMAT)}'"
    )
    print(f"Business       : {business}")
    print(f"Scraped at     : {scraped_data.get('scraped_at')}")
    print(f"Reference date : {reference_date.strftime(DATE_FORMAT)}\n")

    reviews = _load_reviews(scraped_data)

    review_velocity = _calculate_review_velocity(reviews, reference_date)
    star_rating_decay = _calculate_star_rating_decay(reviews, reference_date)
    response_rate = _calculate_response_rate(reviews, reference_date)

    ddi_reputation_score = round(
        review_velocity["score"] + star_rating_decay["score"] + response_rate["score"],
        2,
    )

    _log_section("DDI Reputation Score — Final Summary")
    logger.info(
        f"review_velocity: review_velocity result = {review_velocity['score']}/{MAX_REVIEW_VELOCITY_SCORE}"
    )
    logger.info(
        f"review_velocity: star_rating_decay result = {star_rating_decay['score']}/{MAX_STAR_RATING_DECAY_SCORE}"
    )
    logger.info(
        f"review_velocity: response_rate result = {response_rate['score']}/{MAX_RESPONSE_RATE_SCORE}"
    )
    logger.info(
        f'review_velocity: "DDI_Reputation_Score_Result": {ddi_reputation_score}'
    )
    logger.info(
        f'review_velocity: "max_DDI_Reputation_Score": {MAX_DDI_REPUTATION_SCORE}'
    )

    print("review_velocity result    =", review_velocity["score"])
    print("star_rating_decay result  =", star_rating_decay["score"])
    print("response_rate result      =", response_rate["score"])
    print(f'    "DDI_Reputation_Score_Result": {ddi_reputation_score}')
    print(f'    "max_DDI_Reputation_Score": {MAX_DDI_REPUTATION_SCORE}\n')

    result = {
        "status": "success",
        "business_name": business_name,
        "business": scraped_data.get("business"),
        "scraped_at": scraped_data.get("scraped_at"),
        "reference_date": reference_date.strftime(DATE_FORMAT),
        "total_reviews": len(reviews),
        "reviews": reviews,
        "review_velocity": review_velocity,
        "star_rating_decay": star_rating_decay,
        "response_rate": response_rate,
        "DDI_Reputation_Score_Result": ddi_reputation_score,
        "max_DDI_Reputation_Score": MAX_DDI_REPUTATION_SCORE,
    }

    _log_section("Reputation Score analysis completed")
    logger.info(
        f"review_velocity: final results for '{business}' — "
        f"DDI={ddi_reputation_score}/{MAX_DDI_REPUTATION_SCORE}"
    )

    return result
