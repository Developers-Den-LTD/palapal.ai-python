import json

from services.logger_services import logger
from services.s3_service import (
    ddi_score_exists_in_s3,
    download_ddi_score_result_from_s3,
    get_ddi_score_s3_key,
)
from utils.scraped_result_paths import get_ddi_score_result_path


def _load_local_ddi_score(business_name: str, local_path) -> dict:
    with open(local_path, "r", encoding="utf-8") as file:
        return json.load(file)


def _score_percentage(score, max_score) -> float:
    if not max_score:
        return 0.0
    return (float(score or 0) / float(max_score)) * 100


def _color_from_percentage(percentage: float) -> str:
    if percentage >= 85:
        return "green"
    if percentage >= 40:
        return "amber"
    return "red"


def _card(message: str, *, score=None, max_score=None, percentage: float | None = None) -> dict:
    pct = percentage if percentage is not None else _score_percentage(score, max_score)
    return {
        "message": message,
        "color": _color_from_percentage(pct),
    }


def _with_diy_tip(card: dict, diy_tip: str | None) -> dict:
    """
    Attach DIY_TIP only when the card is red/amber.
    (Green cards stay clean and tip-free.)
    """
    if not diy_tip:
        return card
    if card.get("color") in ("red", "amber"):
        card["DIY_TIP"] = diy_tip
    return card


def _format_citation_card(data: dict | None) -> dict:
    data = data or {}
    mentions = data.get("mentions", 0)
    total = data.get("total_answers") or data.get("total_questions", 40)
    message = f"{mentions} AI mentioned out of {total}"
    percentage = data.get("percentage")
    if percentage is None:
        percentage = _score_percentage(data.get("score"), data.get("max_score"))
    return _card(message, percentage=float(percentage))


def _format_exposure_card(data: dict | None) -> dict:
    data = data or {}
    avg_position = data.get("average_position", "not found")
    if avg_position == "not found":
        message = "Business not found in AI answer lists"
    else:
        message = f"Average position in AI answers: {avg_position}"
    return _card(message, score=data.get("score"), max_score=data.get("max_score"))


def _format_sentiment_card(data: dict | None) -> dict:
    data = data or {}
    positive_pct = data.get("positive_percentage", 0)
    total = data.get("total_comments", 0)
    message = f"{positive_pct}% positive sentiment from {total} reviews"
    return _card(message, score=data.get("score"), max_score=data.get("max_score"))


def _format_review_velocity_card(data: dict | None) -> dict:
    data = data or {}
    count = data.get("reviews_last_30_days", 0)
    label = data.get("label", "No data")
    message = f"{count} reviews in last 30 days ({label})"
    return _card(message, score=data.get("score"), max_score=data.get("max_score"))


def _format_star_rating_decay_card(data: dict | None) -> dict:
    data = data or {}
    rating = data.get("weighted_average_rating", 0)
    message = f"Weighted average rating: {rating}/5"
    return _card(message, score=data.get("score"), max_score=data.get("max_score"))


def _format_response_rate_card(data: dict | None) -> dict:
    data = data or {}
    rate_pct = data.get("response_rate_pct", 0)
    replied = data.get("replied_reviews", 0)
    total = data.get("reviews_last_90_days", 0)
    message = f"{rate_pct}% response rate ({replied} of {total} reviews replied)"
    return _card(message, score=data.get("score"), max_score=data.get("max_score"))


def _format_technical_foundation_card(technical: dict | None) -> dict:
    technical = technical or {}
    pagespeed = technical.get("pagespeed_score") or {}
    score = pagespeed.get("score", 0)
    max_score = pagespeed.get("max_score", 4)
    message = f"PageSpeed score: {score}/{max_score}"
    return _card(message, score=score, max_score=max_score)


def _format_llms_txt_card(data: dict | None) -> dict:
    data = data or {}
    message = data.get("message") or "llms.txt not checked"
    return _card(message, score=data.get("score"), max_score=data.get("max_score"))


def _format_json_ld_card(data: dict | None) -> dict:
    data = data or {}
    message = data.get("message") or "JSON-LD schema not checked"
    return _card(message, score=data.get("score"), max_score=data.get("max_score"))


def _format_nap_consistency_card(data: dict | None) -> dict:
    data = data or {}
    platforms = data.get("platforms") or {}

    if data.get("consistent"):
        reference = (
            platforms.get("google_maps")
            or platforms.get("yelp")
            or platforms.get("tripadvisor")
            or {}
        )
        name = reference.get("name", "N/A")
        address = reference.get("address", "N/A")
        phone = reference.get("phone", "N/A")
        message = f"Name {name}, Address {address}, Phone {phone}"
    else:
        message = data.get("message") or "NAP mismatch detected"

    return _card(message, score=data.get("score"), max_score=data.get("max_score"))


def _build_action_cards(ddi_result: dict | None) -> dict:
    """
    Convert the stored DDI result into action cards with message + color.

    Color thresholds (based on score percentage):
      - green : >= 85%
      - amber : 40% to 84%
      - red   : < 40%
    """
    ddi_result = ddi_result or {}

    ai_visibility = ddi_result.get("ai_visibility") or {}
    reputation = ddi_result.get("reputation") or {}
    technical = ddi_result.get("technical_foundation") or {}

    return {
        "citation_score": _with_diy_tip(
            _format_citation_card(ai_visibility.get("citation_score")),
            "Improve your website/service pages with clear category + location text "
            "(e.g., 'best pizza in <city>'), add FAQs, and publish consistent local content "
            "so AI has more reasons to mention you.",
        ),
        "exposure_fairness": _with_diy_tip(
            _format_exposure_card(ai_visibility.get("exposure_fairness")),
            "Strengthen authority signals: complete your Google Business Profile, keep NAP consistent "
            "across directories, earn high-quality local backlinks/citations, and improve on-page "
            "About/Services/Location content so you appear earlier in AI lists.",
        ),
        "sentiment_analysis": _with_diy_tip(
            _format_sentiment_card(ai_visibility.get("sentiment_analysis")),
            "Ask happy customers for reviews, respond to negative reviews quickly, fix repeated complaints, "
            "and encourage detailed feedback (service, cleanliness, delivery time) to improve sentiment.",
        ),
        "review_velocity": _with_diy_tip(
            _format_review_velocity_card(reputation.get("review_velocity")),
            "Increase review volume: use QR codes at checkout, send post-purchase WhatsApp/SMS review links, "
            "and remind customers to leave feedback (no paid reviews).",
        ),
        "star_rating_decay": _with_diy_tip(
            _format_star_rating_decay_card(reputation.get("star_rating_decay")),
            "Focus on improving recent ratings: fix current service issues, request reviews right after great experiences, "
            "and improve consistency so newer reviews trend higher.",
        ),
        "response_rate": _with_diy_tip(
            _format_response_rate_card(reputation.get("response_rate")),
            "Reply to reviews (especially recent and negative) within 24–48 hours, use saved templates, "
            "and assign a team member to respond daily to raise response rate.",
        ),
        "technical_foundation": _with_diy_tip(
            _format_technical_foundation_card(technical),
            "Improve site speed and basics: compress images, enable caching, reduce unused JavaScript, "
            "and use fast hosting/CDN. Aim for strong mobile performance.",
        ),
        "llms_txt": _with_diy_tip(
            _format_llms_txt_card(technical.get("llms_txt")),
            "Add `llms.txt` at your site root with a short business summary, key pages (menu/services), "
            "and contact/location info so AI systems can understand your site.",
        ),
        # "json_ld_schema": _with_diy_tip(
        #     _format_json_ld_card(technical.get("json_ld")),
        #     "Add JSON-LD schema (e.g., LocalBusiness/Restaurant) with name, address, phone, openingHours, "
        #     "geo, sameAs links, and (if available) ratings/reviews.",
        # ),
        "nap_consistency": _with_diy_tip(
            _format_nap_consistency_card(technical.get("nap_consistency")),
            "Make Name/Address/Phone identical across Google, Yelp, TripAdvisor, Facebook, and your website. "
            "Fix formatting differences and remove duplicates to improve consistency.",
        ),
    }


def get_action_cards_data(business_name: str) -> dict:
    """
    Load DDI score result for a business.
    Uses local DDI_score/<slug>/Result.json first; downloads from S3 if missing.
    """
    business_name = business_name.strip()
    local_path = get_ddi_score_result_path(business_name)
    s3_key = get_ddi_score_s3_key(business_name)

    logger.info(
        f"action_cards: request received business='{business_name}', "
        f"local_path='{local_path}', s3_key='{s3_key}'"
    )

    if local_path.exists():
        data = _load_local_ddi_score(business_name, local_path)
        cards = _build_action_cards(data)
        logger.info(
            f"action_cards: loaded from local cache — business='{business_name}', "
            f"path='{local_path}'"
        )
        logger.info(
            "action_cards: result ready — "
            f"business='{business_name}', source='local'"
        )
        return {
            "status": "success",
            "business_name": business_name,
            "source": "local",
            "local_path": str(local_path),
            "s3_key": s3_key,
            "cards": cards,
        }

    logger.info(
        f"action_cards: not found locally, checking S3 — business='{business_name}'"
    )

    if not ddi_score_exists_in_s3(business_name):
        logger.warning(
            f"action_cards: no DDI score found locally or in S3 for '{business_name}'"
        )
        return {
            "status": "error",
            "message": f"No DDI score result found for '{business_name}'.",
            "business_name": business_name,
            "local_path": str(local_path),
            "s3_key": s3_key,
            "cards": None,
        }

    if not download_ddi_score_result_from_s3(business_name, local_path):
        logger.error(
            f"action_cards: failed to download DDI score from S3 for '{business_name}'"
        )
        return {
            "status": "error",
            "message": f"Failed to download DDI score result for '{business_name}' from S3.",
            "business_name": business_name,
            "local_path": str(local_path),
            "s3_key": s3_key,
            "cards": None,
        }

    data = _load_local_ddi_score(business_name, local_path)
    cards = _build_action_cards(data)
    logger.info(
        f"action_cards: downloaded from S3 and cached locally — "
        f"business='{business_name}', path='{local_path}'"
    )
    logger.info(
        "action_cards: result ready — "
        f"business='{business_name}', source='s3'"
    )

    return {
        "status": "success",
        "business_name": business_name,
        "source": "s3",
        "local_path": str(local_path),
        "s3_key": s3_key,
        "cards": cards,
    }
