"""
STEP 1 & 2 of the llms.txt pipeline: find pages on a website, then pick the best ones.

Flow:
  1. Recursively crawl the site (same domain, follow links page by page).
  2. Remove junk URLs (login, images, privacy, etc.).
  3. Rank remaining URLs and keep the most useful ones for llms.txt.
"""

import re
from urllib.parse import urlparse, urlunparse

from services.logger_services import logger
from services.url_finder_services import crawl_website

# How many pages the recursive crawler will visit before filtering.
MAX_DISCOVERED_URLS = 100
MAX_SELECTED_URLS = 25
# Pause between crawl requests (seconds) — 0 with parallel workers is usually fine.
CRAWL_DELAY_SECONDS = 0.0
DEFAULT_CRAWL_WORKERS = 8

# URL path patterns we skip — not useful for llms.txt (login, cart, legal, etc.).
EXCLUDE_PATH_REGEXES = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"/login",
        r"/signin",
        r"/sign-in",
        r"/signup",
        r"/sign-up",
        r"/register",
        r"/cart",
        r"/checkout",
        r"/account",
        r"/wp-admin",
        r"/wp-login",
        r"/tag/",
        r"/author/",
        r"/feed",
        r"/search",
        r"/privacy",
        r"/terms",
        r"/cookie",
        r"/legal",
        r"/disclaimer",
        r"/dmca",
        r"/unsubscribe",
        r"/newsletter",
        r"\?page=",
        r"[?&]sort=",
        r"[?&]filter=",
    )
)

# File types we skip — images, PDFs, scripts are not HTML pages.
EXCLUDE_EXTENSIONS = (
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".zip",
    ".rar",
    ".mp4",
    ".mp3",
    ".css",
    ".js",
    ".xml",
    ".json",
)

# Words in a URL that make it more important (menu, contact, about, etc.).
PRIORITY_KEYWORDS = (
    "menu",
    "about",
    "contact",
    "service",
    "location",
    "order",
    "booking",
    "reserve",
    "hours",
    "pricing",
    "price",
    "deal",
    "offer",
    "gallery",
    "team",
    "faq",
    "delivery",
    "takeaway",
    "restaurant",
    "hotel",
)


def _log_section(title: str) -> None:
    logger.info(f"llms_txt_crawler: {'=' * 60}")
    logger.info(f"llms_txt_crawler: {title}")
    logger.info(f"llms_txt_crawler: {'=' * 60}")


def normalize_website_url(website_url: str) -> str:
    """Turn user input into a clean root URL like https://example.com."""
    website_url = website_url.strip()
    parsed = urlparse(website_url)
    if not parsed.scheme:
        website_url = f"https://{website_url}"
        parsed = urlparse(website_url)
    if not parsed.netloc:
        raise ValueError(f"Invalid website URL: {website_url}")
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def _canonical_url_key(url: str) -> str:
    """Normalize URL for deduplication (e.g. /page and /page/ are the same)."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", "", ""))


def _same_domain(url: str, base_netloc: str) -> bool:
    """Only keep links on the same website (ignore external sites)."""
    netloc = urlparse(url).netloc.lower()
    base = base_netloc.lower()
    if netloc == base:
        return True
    if netloc.startswith("www.") and netloc[4:] == base:
        return True
    if base.startswith("www.") and netloc == base[4:]:
        return True
    return False


def _should_exclude_url(url: str) -> bool:
    """Return True if this URL should not be used for llms.txt."""
    parsed = urlparse(url)
    path = (parsed.path or "").lower()

    for extension in EXCLUDE_EXTENSIONS:
        if path.endswith(extension):
            return True

    for pattern in EXCLUDE_PATH_REGEXES:
        if pattern.search(url):
            return True

    return False


def filter_unnecessary_urls(base_url: str, urls: list[str]) -> tuple[list[str], list[str]]:
    """
    Remove URLs that are not useful for llms.txt (login, signup, cart, etc.).

    Returns (kept_urls, excluded_urls).
    """
    base_netloc = urlparse(base_url).netloc
    kept: list[str] = []
    excluded: list[str] = []
    seen: set[str] = set()

    for url in urls:
        canonical = _canonical_url_key(url)
        if canonical in seen:
            continue
        seen.add(canonical)

        if not _same_domain(url, base_netloc):
            excluded.append(url)
            continue
        if _should_exclude_url(url):
            excluded.append(url)
            continue
        kept.append(url)

    return kept, excluded


def _score_url(url: str) -> int:
    """Higher score = more important page for llms.txt."""
    path = urlparse(url).path.lower()
    score = 0

    if path == "/" or path == "":
        score += 50

    for keyword in PRIORITY_KEYWORDS:
        if keyword in path:
            score += 10

    depth = path.count("/")
    score -= depth

    return score


def crawl_website_urls(
    website_url: str,
    max_pages: int = MAX_DISCOVERED_URLS,
    delay_seconds: float = CRAWL_DELAY_SECONDS,
    max_workers: int = DEFAULT_CRAWL_WORKERS,
    prefer_sitemap: bool = True,
) -> dict:
    """
    Crawl a website and return internal URLs useful for llms.txt.

    Unnecessary URLs (login, signup, cart, legal pages, etc.) are removed
    after discovery.

    Used by POST /api/crawl-website-urls before llms.txt generation.
    """
    _log_section("URL discovery (recursive crawl)")
    base_url = normalize_website_url(website_url)
    logger.info(
        f"llms_txt_crawler: base_url='{base_url}', "
        f"max_pages={max_pages}, delay={delay_seconds}s, "
        f"max_workers={max_workers}, prefer_sitemap={prefer_sitemap}"
    )

    discovered_urls, discovery_method = crawl_website(
        base_url,
        max_pages=max_pages,
        delay_seconds=delay_seconds,
        max_workers=max_workers,
        prefer_sitemap=prefer_sitemap,
    )

    _log_section("Filter unnecessary URLs")
    filtered_urls, excluded_urls = filter_unnecessary_urls(base_url, discovered_urls)

    logger.info(
        f"llms_txt_crawler: discovered {len(discovered_urls)} URL(s) via {discovery_method}, "
        f"kept {len(filtered_urls)}, excluded {len(excluded_urls)}"
    )
    return {
        "status": "success",
        "website_url": base_url,
        "domain": urlparse(base_url).netloc,
        "max_pages": max_pages,
        "delay_seconds": delay_seconds,
        "max_workers": max_workers,
        "prefer_sitemap": prefer_sitemap,
        "discovery_method": discovery_method,
        "discovered_url_count": len(discovered_urls),
        "excluded_url_count": len(excluded_urls),
        "url_count": len(filtered_urls),
        "urls": filtered_urls,
        "excluded_urls": excluded_urls,
    }


def discover_website_urls(website_url: str) -> tuple[str, list[str]]:
    """
    STEP 1: Recursively crawl the website starting from the root URL.

    Returns all discovered URLs (before filtering) for pipeline metadata.
    """
    base_url = normalize_website_url(website_url)
    urls, _ = crawl_website(
        base_url,
        max_pages=MAX_DISCOVERED_URLS,
        delay_seconds=CRAWL_DELAY_SECONDS,
        max_workers=DEFAULT_CRAWL_WORKERS,
        prefer_sitemap=True,
    )
    return base_url, urls


def filter_and_rank_urls(base_url: str, urls: list[str]) -> list[str]:
    """
    STEP 2: Remove bad URLs, sort by importance, and pick up to MAX_SELECTED_URLS.
    """
    _log_section("Step 2 — Filter and rank URLs")

    filtered, excluded = filter_unnecessary_urls(base_url, urls)
    ranked = sorted(filtered, key=_score_url, reverse=True)
    selected = ranked[:MAX_SELECTED_URLS]

    logger.info(
        f"llms_txt_crawler: filtered {len(filtered)} URL(s), "
        f"excluded {len(excluded)}, selected {len(selected)} for extraction"
    )
    for index, url in enumerate(selected, start=1):
        logger.info(f"llms_txt_crawler: [{index}] {url}")

    return selected
