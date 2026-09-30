"""
Brand context card for AI social content.

Built from the request's business profile and guidelines, enriched with stored
Keyword SEO keywords and review highlights from S3 when they exist. Every
enrichment miss is logged and skipped; none of them fails a request.
"""

import json

from schema.social_content_schema import BusinessContext, SocialContentGuidelines
from services.logger_services import logger
from services.s3_service import (
    download_json_from_s3,
    get_keyword_seo_s3_key,
    load_scraped_result_data,
)
from services.social_content.constants import (
    DEFAULT_GUIDELINES,
    EMOJI_CAPS,
    KEYWORD_PRIORITY_ORDER,
    MAX_ENRICHED_KEYWORDS,
    MAX_HIGHLIGHT_CHARS,
    MAX_REVIEW_HIGHLIGHTS,
    POST_LENGTH_HINTS,
    REVIEW_PLATFORMS,
)


def user_input(text: str | None) -> str:
    """Wrap caller-supplied text so the model treats it as data."""
    return f"<user_input>{text or ''}</user_input>"


def keywords_from_keyword_seo(result: dict | None) -> list[str]:
    """Keywords from a stored Keyword SEO result, highest priority first."""
    if not isinstance(result, dict):
        return []
    attempts = result.get("attempts")
    if not isinstance(attempts, list):
        return []
    items = [item for item in attempts if isinstance(item, dict) and str(item.get("keyword") or "").strip()]
    items.sort(key=lambda item: KEYWORD_PRIORITY_ORDER.get(str(item.get("priority") or "").lower(), 99))
    keywords: list[str] = []
    seen: set[str] = set()
    for item in items:
        keyword = str(item["keyword"]).strip()
        if keyword.lower() in seen:
            continue
        seen.add(keyword.lower())
        keywords.append(keyword)
        if len(keywords) >= MAX_ENRICHED_KEYWORDS:
            break
    return keywords


def _review_rating(value) -> float | None:
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return None
    # Same rule as scrapper_services: a rating above 5 is on a 10x scale (e.g. 45 for 4.5).
    return rating / 10 if rating > 5 else rating


def review_highlights(scraped: dict) -> list[str]:
    """Up to MAX_REVIEW_HIGHLIGHTS review texts rated 4-5 stars."""
    highlights: list[str] = []
    if not isinstance(scraped, dict):
        return highlights
    for platform in REVIEW_PLATFORMS:
        platform_data = scraped.get(platform)
        reviews = platform_data.get("reviews") if isinstance(platform_data, dict) else None
        if not isinstance(reviews, list):
            continue
        for review in reviews:
            if not isinstance(review, dict):
                continue
            rating = _review_rating(review.get("rating"))
            if rating is None or rating < 4:
                continue
            text = review.get("comment") or review.get("text")
            if isinstance(text, dict):
                text = text.get("full")
            text = str(text or "").strip()
            if not text:
                continue
            highlights.append(text[:MAX_HIGHLIGHT_CHARS])
            if len(highlights) >= MAX_REVIEW_HIGHLIGHTS:
                return highlights
    return highlights


def _load_keywords(business: BusinessContext) -> tuple[list[str], str]:
    if business.keywords:
        return business.keywords, "request"
    s3_key = get_keyword_seo_s3_key(business.business_name, business.business_id)
    keywords = keywords_from_keyword_seo(download_json_from_s3(s3_key=s3_key))
    if keywords:
        logger.info(
            f"social_content [context]: keywords loaded from Keyword SEO — count={len(keywords)}, key='{s3_key}'"
        )
        return keywords, "keyword_seo"
    logger.info(f"social_content [context]: no keywords in request or Keyword SEO — key='{s3_key}'")
    return [], "none"


def _load_highlights(business: BusinessContext) -> list[str]:
    try:
        scraped = load_scraped_result_data(business.business_name, business.business_id)
    except FileNotFoundError:
        logger.info(
            "social_content [context]: no scraped reviews for this business — "
            f"business='{business.business_name}', business_id='{business.business_id}'"
        )
        return []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"social_content [context]: scraped reviews unreadable, skipping highlights — {exc}")
        return []
    highlights = review_highlights(scraped)
    logger.info(f"social_content [context]: review highlights — count={len(highlights)}")
    return highlights


def guidelines_block(guidelines: SocialContentGuidelines | None) -> str:
    g = guidelines or SocialContentGuidelines()
    tone = g.brand_tone or DEFAULT_GUIDELINES["brand_tone"]
    length = g.post_length or DEFAULT_GUIDELINES["post_length"]
    emoji = g.emoji_usage or DEFAULT_GUIDELINES["emoji_usage"]
    emoji_cap = EMOJI_CAPS[emoji]
    lines = [
        f"- Brand tone: {user_input(tone)}",
        f"- Caption length: {length} ({POST_LENGTH_HINTS[length]})",
        f"- Emoji: {emoji}" + (f" (at most {emoji_cap} per caption)" if emoji_cap is not None else ""),
    ]
    if g.writing_style:
        lines.append(f"- Writing style: {user_input(g.writing_style)}")
    if g.target_audience:
        lines.append(f"- Target audience: {user_input(g.target_audience)}")
    if g.hashtags:
        if g.hashtags.max_count is not None:
            lines.append(f"- At most {g.hashtags.max_count} hashtags")
        if g.hashtags.always_include:
            lines.append(f"- Always include hashtags: {user_input(', '.join(g.hashtags.always_include))}")
        if g.hashtags.never_include:
            lines.append(f"- Never use hashtags: {user_input(', '.join(g.hashtags.never_include))}")
    if g.cta:
        if g.cta.preferred:
            lines.append(f"- Preferred calls to action: {user_input(', '.join(g.cta.preferred))}")
        lines.append(f"- Call to action required: {'yes' if g.cta.required else 'no'}")
    if g.restricted_words:
        lines.append(f"- Never use these words: {user_input(', '.join(g.restricted_words))}")
    if g.required_terms:
        lines.append(f"- Always include these terms: {user_input(', '.join(g.required_terms))}")
    return "\n".join(lines)


def build_context_card(
    business: BusinessContext,
    guidelines: SocialContentGuidelines | None,
    *,
    include_reviews: bool,
) -> dict:
    """Return {"text", "keywords", "keyword_source", "highlights"}."""
    keywords, keyword_source = _load_keywords(business)
    highlights = _load_highlights(business) if include_reviews else []

    lines = [
        f"Business name: {user_input(business.business_name)}",
        f"Industry: {user_input(business.industry)}",
        f"Services: {user_input(', '.join(business.services))}",
        f"Location: {user_input(business.location)}",
    ]
    if business.description:
        lines.append(f"About: {user_input(business.description)}")
    if keywords:
        lines.append(f"Target keywords: {user_input(', '.join(keywords))}")
    if highlights:
        lines.append("What customers say (review highlights):")
        lines.extend(f"- {user_input(text)}" for text in highlights)
    if business.recent_captions:
        lines.append("Past captions (match this voice, do not copy):")
        lines.extend(f"- {user_input(caption)}" for caption in business.recent_captions)
    lines.append("Brand guidelines:")
    lines.append(guidelines_block(guidelines))

    logger.info(
        "social_content [context]: card built — "
        f"business='{business.business_name}', keywords={len(keywords)} ({keyword_source}), "
        f"highlights={len(highlights)}, recent_captions={len(business.recent_captions)}, "
        f"has_guidelines={guidelines is not None}"
    )
    return {
        "text": "\n".join(lines),
        "keywords": keywords,
        "keyword_source": keyword_source,
        "highlights": highlights,
    }
