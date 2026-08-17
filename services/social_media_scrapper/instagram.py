import asyncio
from urllib.parse import urlparse

from services.logger_services import logger
from services.scrapper_services import _run_actor_with_retry
from services.social_media_scrapper.common import (
    analyze_tone_of_voice,
    extract_address_from_bio,
    list_actor_items,
    log_section,
    unique_strings,
)

INSTAGRAM_PROFILE_ACTOR = "apify/instagram-scraper"
INSTAGRAM_CONTACT_ACTOR = "seemuapps/instagram-contact-scraper"
LOG_INSTAGRAM = "socialmedia [instagram]"

_INVALID_INSTAGRAM_SEGMENTS = frozenset(
    {"p", "reel", "reels", "explore", "stories", "tv", "accounts"}
)


def extract_instagram_username(instagram_url: str) -> str:
    """Extract Instagram username from a profile URL."""
    parsed = urlparse(instagram_url.strip())
    path = (parsed.path or "").strip("/")
    if not path:
        raise ValueError("Instagram URL must include a profile username")

    parts = [part for part in path.split("/") if part]
    if parts[0] == "_u" and len(parts) >= 2:
        username = parts[1]
    else:
        username = parts[0]

    username = username.lstrip("@").strip()
    if not username:
        raise ValueError("Could not extract Instagram username from URL")

    if username.lower() in _INVALID_INSTAGRAM_SEGMENTS:
        raise ValueError(
            "Instagram URL must be a profile URL, not a post, reel, or explore link"
        )

    return username


def _instagram_actor_input(
    instagram_url: str,
    posts_limit: int,
    results_type: str,
) -> dict:
    return {
        "addParentData": False,
        "directUrls": [instagram_url],
        "resultsLimit": posts_limit,
        "resultsType": results_type,
        "searchLimit": posts_limit,
        "searchType": "hashtag",
    }


def _scrape_profile(instagram_url: str, posts_limit: int) -> dict | None:
    """Instagram: run apify/instagram-scraper for profile details."""
    logger.info(
        f"{LOG_INSTAGRAM}: calling actor '{INSTAGRAM_PROFILE_ACTOR}' "
        f"(profile details) url='{instagram_url}', posts_limit={posts_limit}"
    )
    run = _run_actor_with_retry(
        INSTAGRAM_PROFILE_ACTOR,
        _instagram_actor_input(instagram_url, posts_limit, "details"),
    )
    items = list_actor_items(run)
    logger.info(f"{LOG_INSTAGRAM}: profile actor returned {len(items)} item(s)")
    return items[0] if items else None


def _scrape_posts(instagram_url: str, posts_limit: int) -> list[dict]:
    """Instagram: fallback posts scrape when profile details omit latestPosts."""
    logger.info(
        f"{LOG_INSTAGRAM}: calling actor '{INSTAGRAM_PROFILE_ACTOR}' "
        f"(posts fallback) url='{instagram_url}', posts_limit={posts_limit}"
    )
    run = _run_actor_with_retry(
        INSTAGRAM_PROFILE_ACTOR,
        _instagram_actor_input(instagram_url, posts_limit, "posts"),
    )
    items = list_actor_items(run)
    logger.info(f"{LOG_INSTAGRAM}: posts actor returned {len(items)} item(s)")
    return items


def _scrape_contacts(username: str) -> dict | None:
    """Instagram: run seemuapps/instagram-contact-scraper for contact details."""
    logger.info(
        f"{LOG_INSTAGRAM}: calling actor '{INSTAGRAM_CONTACT_ACTOR}' "
        f"username='{username}'"
    )
    run = _run_actor_with_retry(
        INSTAGRAM_CONTACT_ACTOR,
        {
            "usernames": [username],
            "scrapeLinkInBio": True,
        },
    )
    items = list_actor_items(run)
    logger.info(f"{LOG_INSTAGRAM}: contact actor returned {len(items)} item(s)")
    return items[0] if items else None


def _caption_from_post(post: dict) -> dict | None:
    caption = (post.get("caption") or "").strip()
    if not caption:
        return None
    return {
        "caption": caption,
        "url": post.get("url"),
        "timestamp": post.get("timestamp"),
        "likes_count": post.get("likesCount"),
    }


def _extract_captions(
    profile_item: dict | None,
    posts_limit: int,
) -> list[dict]:
    """Instagram: pull captions from latestPosts on the profile details response."""
    if not profile_item:
        return []

    captions = []
    for post in (profile_item.get("latestPosts") or [])[:posts_limit]:
        item = _caption_from_post(post)
        if item:
            captions.append(item)
    return captions


def _extract_captions_from_posts(
    post_items: list[dict],
    posts_limit: int,
) -> list[dict]:
    """Instagram: pull captions from a dedicated posts scrape response."""
    captions = []
    for post in post_items[:posts_limit]:
        item = _caption_from_post(post)
        if item:
            captions.append(item)
    return captions


def _build_contact(contact_item: dict | None) -> dict:
    contact_item = contact_item or {}
    return {
        "emails": unique_strings(contact_item.get("emails") or []),
        "phones": unique_strings(contact_item.get("phones") or []),
        "whatsapp": unique_strings(contact_item.get("whatsapp") or []),
        "ig_public_email": contact_item.get("igPublicEmail"),
        "ig_business_email": contact_item.get("igBusinessEmail"),
        "ig_public_phone": contact_item.get("igPublicPhone"),
        "ig_business_phone": contact_item.get("igBusinessPhone"),
        "external_url": contact_item.get("externalUrl"),
        "biography": contact_item.get("biography"),
    }


def _build_profile(
    profile_item: dict | None,
    contact_item: dict | None,
) -> dict:
    profile_item = profile_item or {}
    contact_item = contact_item or {}
    biography = profile_item.get("biography") or contact_item.get("biography")

    return {
        "name": profile_item.get("fullName"),
        "username": profile_item.get("username"),
        "biography": biography,
        "address": extract_address_from_bio(biography),
        "is_business_account": profile_item.get("isBusinessAccount"),
        "business_category": profile_item.get("businessCategoryName"),
        "external_url": profile_item.get("externalUrl")
        or contact_item.get("externalUrl"),
        "followers_count": profile_item.get("followersCount"),
        "posts_count": profile_item.get("postsCount"),
        "verified": profile_item.get("verified"),
    }


async def scrape_instagram(
    instagram_url: str,
    username: str,
    posts_limit: int = 10,
) -> dict:
    """Scrape Instagram profile + contacts in parallel and return platform block."""
    log_section(LOG_INSTAGRAM, "Starting Instagram scrape")
    logger.info(
        f"{LOG_INSTAGRAM}: username='{username}', url='{instagram_url}', "
        f"posts_limit={posts_limit}"
    )

    profile_task = asyncio.to_thread(_scrape_profile, instagram_url, posts_limit)
    contact_task = asyncio.to_thread(_scrape_contacts, username)
    profile_item, contact_item = await asyncio.gather(profile_task, contact_task)

    captions = _extract_captions(profile_item, posts_limit)
    if not captions and posts_limit > 0:
        latest_posts = (profile_item or {}).get("latestPosts") or []
        posts_count = (profile_item or {}).get("postsCount")
        if not latest_posts:
            logger.warning(
                f"{LOG_INSTAGRAM}: profile response missing latestPosts "
                f"(posts_count={posts_count}); using posts scrape fallback"
            )
            post_items = await asyncio.to_thread(
                _scrape_posts, instagram_url, posts_limit
            )
            captions = _extract_captions_from_posts(post_items, posts_limit)
            logger.info(
                f"{LOG_INSTAGRAM}: posts fallback returned {len(captions)} caption(s)"
            )

    profile = _build_profile(profile_item, contact_item)
    contact = _build_contact(contact_item)

    tone_texts = []
    if profile.get("biography"):
        tone_texts.append(profile["biography"])
    tone_texts.extend(item["caption"] for item in captions)

    tone_of_voice = analyze_tone_of_voice(
        tone_texts,
        platform_tag=LOG_INSTAGRAM,
        source_label="biography and captions",
    )

    logger.info(
        f"{LOG_INSTAGRAM}: scrape complete — captions={len(captions)}, "
        f"name='{profile.get('name')}'"
    )

    return {
        "platform": "instagram",
        "url": instagram_url,
        "username": username,
        "profile": profile,
        "contact": contact,
        "captions": captions,
        "tone_of_voice": tone_of_voice,
    }
