import asyncio
from urllib.parse import urlparse

from services.logger_services import logger
from services.scrapper_services import _run_actor_with_retry
from services.social_media_scrapper.common import (
    analyze_tone_of_voice,
    list_actor_items,
    log_section,
    unique_strings,
)

FACEBOOK_PAGES_ACTOR = "apify/facebook-pages-scraper"
FACEBOOK_POSTS_ACTOR = "apify/facebook-posts-scraper"
LOG_FACEBOOK = "socialmedia [facebook]"


def extract_facebook_page_slug(facebook_url: str) -> str:
    """Extract Facebook page slug from a page URL."""
    parsed = urlparse(facebook_url.strip())
    path = (parsed.path or "").strip("/")
    if not path:
        raise ValueError("Facebook URL must include a page name")

    parts = [part for part in path.split("/") if part]
    blocked = {"pages", "profile.php", "people", "groups", "events", "watch"}
    if parts[0].lower() in blocked:
        raise ValueError(
            "Facebook URL must be a Facebook Page URL, e.g. "
            "https://www.facebook.com/yourpage/"
        )

    slug = parts[0].strip()
    if not slug:
        raise ValueError("Could not extract Facebook page slug from URL")
    return slug


def _facebook_posts_input(facebook_url: str, max_items: int) -> dict:
    return {
        "startUrls": [{"url": facebook_url}],
        "resultsLimit": max_items,
    }


def _scrape_page(facebook_url: str) -> dict | None:
    """Facebook: run apify/facebook-pages-scraper for name, address, contact, links."""
    logger.info(
        f"{LOG_FACEBOOK}: calling actor '{FACEBOOK_PAGES_ACTOR}' "
        f"(page details) url='{facebook_url}'"
    )
    run = _run_actor_with_retry(
        FACEBOOK_PAGES_ACTOR,
        {
            "startUrls": [{"url": facebook_url}],
        },
    )
    items = list_actor_items(run)
    logger.info(f"{LOG_FACEBOOK}: pages actor returned {len(items)} item(s)")
    return items[0] if items else None


def _scrape_posts(
    facebook_url: str,
    max_items: int,
    *,
    label: str = "posts",
) -> list[dict]:
    """Facebook: run apify/facebook-posts-scraper for post text (tone of voice)."""
    logger.info(
        f"{LOG_FACEBOOK}: calling actor '{FACEBOOK_POSTS_ACTOR}' "
        f"({label}) url='{facebook_url}', max_items={max_items}"
    )
    run = _run_actor_with_retry(
        FACEBOOK_POSTS_ACTOR,
        _facebook_posts_input(facebook_url, max_items),
    )
    items = list_actor_items(run)
    logger.info(f"{LOG_FACEBOOK}: {label} actor returned {len(items)} item(s)")
    return items


def _facebook_posts_fallback_urls(
    facebook_url: str,
    page_item: dict | None,
) -> list[str]:
    """Build alternate Facebook URLs to try when the primary posts scrape is empty."""
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: str | None) -> None:
        if not url:
            return
        normalized = url.strip().rstrip("/")
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        urls.append(normalized)

    add(facebook_url)
    page_item = page_item or {}
    add(page_item.get("pageUrl"))
    add(page_item.get("facebookUrl"))

    expanded: list[str] = []
    for url in urls:
        expanded.append(url)
        if not url.endswith("/posts"):
            expanded.append(f"{url}/posts")

    deduped: list[str] = []
    seen = set()
    for url in expanded:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def _scrape_posts_fallback(
    facebook_url: str,
    page_item: dict | None,
    max_items: int,
) -> list[dict]:
    """Facebook: retry posts scrape with alternate page URLs."""
    for url in _facebook_posts_fallback_urls(facebook_url, page_item):
        if url.rstrip("/") == facebook_url.rstrip("/"):
            continue
        items = _scrape_posts(url, max_items, label="posts fallback")
        if items:
            return items
    return []


def _build_profile(page_item: dict | None) -> dict:
    page_item = page_item or {}
    return {
        "name": page_item.get("title") or page_item.get("pageName"),
        "page_name": page_item.get("pageName"),
        "page_id": page_item.get("pageId"),
        "address": page_item.get("address"),
        "intro": page_item.get("intro"),
        "categories": page_item.get("categories"),
        "followers_count": page_item.get("followers"),
        "likes_count": page_item.get("likes"),
        "profile_picture_url": page_item.get("profilePictureUrl"),
        "page_url": page_item.get("pageUrl") or page_item.get("facebookUrl"),
    }


def _build_contact(page_item: dict | None) -> dict:
    page_item = page_item or {}
    emails = []
    if page_item.get("email"):
        emails.append(page_item["email"])

    phones = []
    if page_item.get("phone"):
        phones.append(page_item["phone"])

    return {
        "emails": unique_strings(emails),
        "phones": unique_strings(phones),
        "website": page_item.get("website"),
        "websites": unique_strings(page_item.get("websites") or []),
        "messenger": page_item.get("messenger"),
    }


def _post_text_from_item(item: dict) -> str:
    for key in ("text", "message", "caption", "postText", "description"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_posts(post_items: list[dict], posts_limit: int) -> list[dict]:
    """Facebook: normalize post records from apify/facebook-posts-scraper."""
    posts = []
    for item in post_items[:posts_limit]:
        text = _post_text_from_item(item)
        if not text:
            continue
        posts.append(
            {
                "text": text,
                "url": item.get("postUrl") or item.get("url"),
                "posted_at": item.get("postedAt") or item.get("time"),
                "like_count": item.get("likeCount") or item.get("likes"),
                "comment_count": item.get("commentCount") or item.get("comments"),
                "share_count": item.get("shareCount") or item.get("shares"),
            }
        )
    return posts


async def scrape_facebook(
    facebook_url: str,
    page_slug: str,
    posts_limit: int = 10,
) -> dict:
    """Scrape Facebook page details + posts in parallel and return platform block."""
    log_section(LOG_FACEBOOK, "Starting Facebook scrape")
    logger.info(f"{LOG_FACEBOOK}: page_slug='{page_slug}', url='{facebook_url}'")

    page_task = asyncio.to_thread(_scrape_page, facebook_url)
    posts_task = asyncio.to_thread(_scrape_posts, facebook_url, posts_limit)
    page_item, post_items = await asyncio.gather(page_task, posts_task)

    posts = _extract_posts(post_items, posts_limit)
    if not posts and posts_limit > 0:
        logger.warning(
            f"{LOG_FACEBOOK}: posts scrape returned {len(post_items)} item(s) "
            f"but no post text; using posts URL fallback"
        )
        fallback_items = await asyncio.to_thread(
            _scrape_posts_fallback, facebook_url, page_item, posts_limit
        )
        posts = _extract_posts(fallback_items, posts_limit)
        logger.info(
            f"{LOG_FACEBOOK}: posts fallback returned {len(posts)} post(s)"
        )

    profile = _build_profile(page_item)
    contact = _build_contact(page_item)

    tone_of_voice = analyze_tone_of_voice(
        [post["text"] for post in posts],
        platform_tag=LOG_FACEBOOK,
        source_label="Facebook posts",
    )

    logger.info(
        f"{LOG_FACEBOOK}: scrape complete — posts={len(posts)}, "
        f"name='{profile.get('name')}'"
    )

    return {
        "platform": "facebook",
        "url": facebook_url,
        "page_slug": page_slug,
        "profile": profile,
        "contact": contact,
        "posts": posts,
        "tone_of_voice": tone_of_voice,
    }
