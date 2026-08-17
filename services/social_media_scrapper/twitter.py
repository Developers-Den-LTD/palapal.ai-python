import asyncio
from urllib.parse import urlparse

from services.logger_services import logger
from services.scrapper_services import _run_actor_with_retry
from services.social_media_scrapper.common import (
    analyze_tone_of_voice,
    list_actor_items,
    log_section,
)

TWITTER_ACTOR = "igolaizola/x-twitter-scraper-ppe"
LOG_TWITTER = "socialmedia [twitter]"

_INVALID_TWITTER_SEGMENTS = frozenset(
    {"status", "i", "intent", "hashtag", "search", "home", "explore", "settings"}
)


def extract_twitter_username(twitter_url: str) -> str:
    """Extract X/Twitter handle from a profile URL."""
    parsed = urlparse(twitter_url.strip())
    host = (parsed.netloc or "").lower()
    if host and "twitter.com" not in host and "x.com" not in host:
        raise ValueError("Twitter URL must be an x.com or twitter.com profile URL")

    path = (parsed.path or "").strip("/")
    if not path:
        raise ValueError("Twitter URL must include a profile handle")

    parts = [part for part in path.split("/") if part]
    username = parts[0].lstrip("@").strip()
    if not username:
        raise ValueError("Could not extract Twitter handle from URL")

    if username.lower() in _INVALID_TWITTER_SEGMENTS:
        raise ValueError(
            "Twitter URL must be a profile URL, not a tweet or search link"
        )

    return username


def _twitter_timeline_input(
    username: str,
    max_items: int,
    *,
    mode: str = "strict",
) -> dict:
    handle = username.lstrip("@").strip()
    base = {
        "username": handle,
        "maxItems": max_items,
    }
    if mode == "strict":
        base.update(
            {
                "replies": "exclude",
                "retweets": "exclude",
                "quotes": "exclude",
            }
        )
    elif mode == "search":
        base.update(
            {
                "query": f"from:{handle}",
                "replies": "exclude",
                "retweets": "exclude",
                "quotes": "exclude",
            }
        )
    return base


def _scrape_timeline(
    username: str,
    max_items: int,
    *,
    mode: str = "strict",
) -> list[dict]:
    """Twitter: run igolaizola/x-twitter-scraper-ppe with handle only (no URL)."""
    handle = username.lstrip("@").strip()
    logger.info(
        f"{LOG_TWITTER}: calling actor '{TWITTER_ACTOR}' "
        f"username='{handle}', max_items={max_items}, mode='{mode}'"
    )
    run = _run_actor_with_retry(
        TWITTER_ACTOR,
        _twitter_timeline_input(username, max_items, mode=mode),
    )
    items = list_actor_items(run)
    logger.info(
        f"{LOG_TWITTER}: actor returned {len(items)} item(s) (mode='{mode}')"
    )
    return items


def _scrape_timeline_fallback(username: str, max_items: int) -> list[dict]:
    """Twitter: retry with relaxed filters, then a from:username search."""
    for mode in ("relaxed", "search"):
        items = _scrape_timeline(username, max_items, mode=mode)
        if items:
            return items
    return []


def _profile_snapshot_from_posts(post_items: list[dict]) -> dict:
    """Build a minimal profile snapshot from the first tweet returned by the posts actor."""
    for item in post_items:
        username = (item.get("username") or "").strip()
        if username:
            return {
                "name": item.get("fullname"),
                "username": username,
                "verified": item.get("verified"),
                "profile_url": item.get("userUrl"),
                "profile_image": item.get("avatar"),
            }
    return {}


def _build_profile(profile_snapshot: dict | None) -> dict:
    """Twitter: map fields available from igolaizola/x-twitter-scraper-ppe tweet items."""
    profile_snapshot = profile_snapshot or {}
    return {
        "name": profile_snapshot.get("name"),
        "username": profile_snapshot.get("username"),
        "bio": None,
        "address": None,
        "location": None,
        "followers_count": None,
        "following_count": None,
        "tweet_count": None,
        "verified": profile_snapshot.get("verified"),
        "profile_url": profile_snapshot.get("profile_url"),
        "profile_image": profile_snapshot.get("profile_image"),
    }


def _build_contact() -> dict:
    """Contact fields are not returned by igolaizola/x-twitter-scraper-ppe."""
    return {
        "emails": [],
        "phones": [],
        "website": None,
    }


def _extract_posts(post_items: list[dict], posts_limit: int) -> list[dict]:
    """Twitter: normalize tweet records from igolaizola/x-twitter-scraper-ppe."""
    posts = []
    for item in post_items[:posts_limit]:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        posts.append(
            {
                "text": text,
                "url": item.get("permalink") or item.get("url"),
                "posted_at": item.get("createdAt") or item.get("displayTime"),
                "like_count": item.get("likes"),
                "retweet_count": item.get("retweets"),
                "reply_count": item.get("comments"),
                "view_count": item.get("views"),
            }
        )
    return posts


async def scrape_twitter(
    twitter_url: str,
    username: str,
    posts_limit: int = 10,
) -> dict:
    """Scrape X/Twitter timeline via handle; profile name comes from tweet metadata."""
    log_section(LOG_TWITTER, "Starting Twitter scrape")
    logger.info(
        f"{LOG_TWITTER}: handle='{username}', url='{twitter_url}'"
    )

    post_items = await asyncio.to_thread(_scrape_timeline, username, posts_limit)
    posts = _extract_posts(post_items, posts_limit)
    if not posts and posts_limit > 0:
        logger.warning(
            f"{LOG_TWITTER}: timeline scrape returned {len(post_items)} item(s) "
            f"but no post text; using timeline fallback"
        )
        post_items = await asyncio.to_thread(
            _scrape_timeline_fallback, username, posts_limit
        )
        posts = _extract_posts(post_items, posts_limit)
        logger.info(
            f"{LOG_TWITTER}: timeline fallback returned {len(posts)} post(s)"
        )

    profile_snapshot = _profile_snapshot_from_posts(post_items)
    profile = _build_profile(profile_snapshot)
    contact = _build_contact()

    tone_of_voice = analyze_tone_of_voice(
        [post["text"] for post in posts],
        platform_tag=LOG_TWITTER,
        source_label="X/Twitter posts",
    )

    logger.info(
        f"{LOG_TWITTER}: scrape complete — posts={len(posts)}, "
        f"name='{profile.get('name')}'"
    )

    return {
        "platform": "twitter",
        "url": twitter_url,
        "username": username,
        "profile": profile,
        "contact": contact,
        "posts": posts,
        "tone_of_voice": tone_of_voice,
    }
