import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from schema.socialmedia_schema import SocialMediaRequest
from services.logger_services import logger
from services.social_media_scrapper.facebook import (
    extract_facebook_page_slug,
    scrape_facebook,
)
from services.social_media_scrapper.instagram import (
    extract_instagram_username,
    scrape_instagram,
)
from services.social_media_scrapper.consistency import check_social_media_consistency  # noqa: E501
from services.social_media_scrapper.twitter import (
    extract_twitter_username,
    scrape_twitter,
)
from utils.scraped_result_paths import BASE_DIR, build_scrape_storage_slug

SOCIAL_MEDIA_BASE_PATH = BASE_DIR / "socialmedia"
SCRAPED_AT_FORMAT = "%d %B %Y %H:%M"


@dataclass(frozen=True)
class SocialMediaScrapeContext:
    """Platform identifiers resolved once per scrape request."""

    storage_slug_name: str
    instagram_username: str | None = None
    facebook_page_slug: str | None = None
    twitter_username: str | None = None


def resolve_platform_handles(payload: SocialMediaRequest) -> SocialMediaScrapeContext:
    """Validate URLs and extract platform handles in a single pass."""
    instagram_username = None
    facebook_page_slug = None
    twitter_username = None

    if payload.instagram_url:
        instagram_username = extract_instagram_username(str(payload.instagram_url))
    if payload.facebook_url:
        facebook_page_slug = extract_facebook_page_slug(str(payload.facebook_url))
    if payload.twitter_url:
        twitter_username = extract_twitter_username(str(payload.twitter_url))

    if instagram_username:
        storage_slug_name = instagram_username
    elif facebook_page_slug:
        storage_slug_name = facebook_page_slug
    else:
        storage_slug_name = twitter_username or "unknown"

    return SocialMediaScrapeContext(
        storage_slug_name=storage_slug_name,
        instagram_username=instagram_username,
        facebook_page_slug=facebook_page_slug,
        twitter_username=twitter_username,
    )


def resolve_storage_slug(payload: SocialMediaRequest) -> str:
    """Use Instagram username first, then Facebook page slug, then Twitter handle."""
    return resolve_platform_handles(payload).storage_slug_name


def get_social_media_result_path(
    business_name: str,
    business_id: str | int | None = None,
) -> Path:
    folder_slug = build_scrape_storage_slug(business_name, business_id)
    return SOCIAL_MEDIA_BASE_PATH / f"{folder_slug}.json"


def save_social_media_result(
    data: dict,
    business_name: str,
    business_id: str | int,
) -> str:
    result_path = get_social_media_result_path(business_name, business_id)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = result_path.with_suffix(".json.tmp")

    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)

    os.replace(temp_path, result_path)
    logger.info(f"socialmedia: saved combined result to {result_path}")
    return str(result_path)


async def scrape_social_media(
    payload: SocialMediaRequest,
    handles: SocialMediaScrapeContext,
) -> dict:
    storage_slug = handles.storage_slug_name
    logger.info(
        "socialmedia: starting combined scrape — "
        f"storage_slug='{storage_slug}', business_id='{payload.business_id}', "
        f"posts_limit={payload.posts_limit}, "
        f"instagram={'yes' if payload.instagram_url else 'no'}, "
        f"facebook={'yes' if payload.facebook_url else 'no'}, "
        f"twitter={'yes' if payload.twitter_url else 'no'}"
    )

    posts_limit = payload.posts_limit
    tasks = []
    if payload.instagram_url and handles.instagram_username:
        tasks.append(
            scrape_instagram(
                str(payload.instagram_url),
                handles.instagram_username,
                posts_limit=posts_limit,
            )
        )
    if payload.facebook_url and handles.facebook_page_slug:
        tasks.append(
            scrape_facebook(
                str(payload.facebook_url),
                handles.facebook_page_slug,
                posts_limit=posts_limit,
            )
        )
    if payload.twitter_url and handles.twitter_username:
        tasks.append(
            scrape_twitter(
                str(payload.twitter_url),
                handles.twitter_username,
                posts_limit=posts_limit,
            )
        )
    platform_results = await asyncio.gather(*tasks)

    instagram_data = None
    facebook_data = None
    twitter_data = None
    for block in platform_results:
        platform = block["platform"]
        if platform == "instagram":
            instagram_data = block
        elif platform == "facebook":
            facebook_data = block
        elif platform == "twitter":
            twitter_data = block

    result = {
        "status": "success",
        "storage_slug": storage_slug,
        "business_id": str(payload.business_id),
        "instagram": instagram_data,
        "facebook": facebook_data,
        "twitter": twitter_data,
        "scraped_at": datetime.now().strftime(SCRAPED_AT_FORMAT),
    }
    result["consistency"] = await check_social_media_consistency(result)

    saved_to = save_social_media_result(
        result,
        storage_slug,
        payload.business_id,
    )
    result["saved_to"] = saved_to

    logger.info(
        "socialmedia: combined scrape complete — "
        f"storage_slug='{storage_slug}', "
        f"instagram={'scraped' if instagram_data else 'skipped'}, "
        f"facebook={'scraped' if facebook_data else 'skipped'}, "
        f"twitter={'scraped' if twitter_data else 'skipped'}, "
        f"saved_to='{saved_to}'"
    )
    return result
