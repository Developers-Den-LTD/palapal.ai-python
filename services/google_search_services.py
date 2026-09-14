"""
Google Search visibility via Apify apify/google-search-scraper.

For each keyword:
- scrape up to KEYWORD_SCRAPE_ATTEMPTS times
- use maxPagesPerQuery = 2
- if the site appears in any attempt → found
- keep the best (lowest absolute) position across attempts
"""

import re
import time
from urllib.parse import urlparse, urlunparse

from services.logger_services import logger
from services.scrapper_services import _run_actor_with_retry
from services.social_media_scrapper.common import list_actor_items

GOOGLE_SEARCH_ACTOR = "apify/google-search-scraper"
MAX_SEARCH_VISIBILITY_SCORE = 15
KEYWORD_SCRAPE_ATTEMPTS = 2
MAX_PAGES_PER_QUERY = 2
RETRY_DELAY_SECONDS = 2

# Google displayedUrl values sometimes look like:
# "https://www.example.com › path › page"
_DISPLAYED_URL_SPLIT_RE = re.compile(r"[›\|>…]|\.{3}")


def clean_url(url: str) -> str:
    """
    Clean a URL before comparison.

    - trim whitespace / quotes
    - handle Google displayedUrl junk (›, ...)
    - add https:// when scheme is missing
    - lowercase host, strip www.
    - drop userinfo, default ports, query, and fragment
    - drop trailing slash on non-root paths
    """
    raw = (url or "").strip().strip("'\"")
    if not raw:
        return ""

    # Prefer the left part of Google breadcrumb-style displayed URLs.
    raw = _DISPLAYED_URL_SPLIT_RE.split(raw, maxsplit=1)[0].strip()
    if not raw:
        return ""

    # Spaces in bad pasted URLs → take first token.
    if " " in raw:
        raw = raw.split()[0].strip()

    if not re.match(r"^https?://", raw, flags=re.IGNORECASE):
        raw = "https://" + raw.lstrip("/")

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower().strip()
    if not host:
        # urlparse can fail on odd inputs; try netloc without credentials/port.
        netloc = (parsed.netloc or "").lower().strip()
        if "@" in netloc:
            netloc = netloc.rsplit("@", 1)[-1]
        host = netloc.split(":")[0].strip()

    if host.startswith("www."):
        host = host[4:]

    if not host:
        return ""

    path = parsed.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return urlunparse(("https", host, path, "", "", ""))


def normalize_host(url: str) -> str:
    """Clean URL, then return lowercase host without leading www."""
    cleaned = clean_url(url)
    if not cleaned:
        return ""
    return (urlparse(cleaned).hostname or "").lower().strip()


def host_matches_website(result_url: str, website_url: str) -> bool:
    """True if cleaned result URL belongs to the cleaned business website domain."""
    owned = normalize_host(website_url)
    result_host = normalize_host(result_url)
    if not owned or not result_host:
        return False
    return result_host == owned or result_host.endswith(f".{owned}")


def score_search_visibility(position: int | None) -> dict:
    """Map organic position to search-visibility points."""
    if position is None:
        score = 0
        band = "missing"
    elif 1 <= position <= 3:
        score = MAX_SEARCH_VISIBILITY_SCORE
        band = "high"
    elif 4 <= position <= 10:
        score = 10
        band = "medium"
    else:
        score = 6
        band = "low"

    return {
        "score": score,
        "max_score": MAX_SEARCH_VISIBILITY_SCORE,
        "band": band,
    }


def _normalize_query_key(text: str) -> str:
    return " ".join((text or "").lower().split())


def _serp_page_number(serp: dict) -> int:
    search_query = serp.get("searchQuery") or {}
    if not isinstance(search_query, dict):
        return 1
    try:
        page = int(search_query.get("page") or 1)
    except (TypeError, ValueError):
        return 1
    return page if page >= 1 else 1


def _find_owned_organic_result(
    organic_results: list[dict],
    website_url: str,
) -> dict | None:
    """Return the best (lowest position) organic result for website_url."""
    cleaned_website = clean_url(website_url)
    if not cleaned_website:
        return None

    best: dict | None = None

    for item in organic_results:
        if not isinstance(item, dict):
            continue

        raw_url = str(item.get("url") or item.get("displayedUrl") or "").strip()
        cleaned_result_url = clean_url(raw_url)
        if not cleaned_result_url:
            continue

        if not host_matches_website(cleaned_result_url, cleaned_website):
            continue

        try:
            position = int(item.get("position"))
        except (TypeError, ValueError):
            continue

        if position < 1:
            continue

        if best is None or position < best["position"]:
            best = {
                "position": position,
                "url": cleaned_result_url,
                "raw_url": raw_url,
                "title": str(item.get("title") or "").strip() or None,
                "displayed_url": str(item.get("displayedUrl") or "").strip() or None,
            }

    return best


def _merge_organic_results_absolute(
    matching_items: list[dict],
    *,
    fallback_keyword: str,
) -> tuple[str, list[dict]]:
    """
    Merge multi-page SERP items for one keyword.

    Apify page positions usually restart at 1 on each page, so we assign
    absolute positions in page order (1, 2, ... across page 1 then page 2).
    """
    if not matching_items:
        return fallback_keyword, []

    sorted_items = sorted(matching_items, key=_serp_page_number)
    query_used = fallback_keyword
    merged: list[dict] = []
    absolute_position = 0

    for serp in sorted_items:
        search_query = serp.get("searchQuery") or {}
        if isinstance(search_query, dict) and search_query.get("term"):
            query_used = str(search_query.get("term")).strip() or fallback_keyword

        page_organics = serp.get("organicResults") or []
        if not isinstance(page_organics, list):
            continue

        for item in page_organics:
            if not isinstance(item, dict):
                continue
            absolute_position += 1
            row = dict(item)
            row["page"] = _serp_page_number(serp)
            row["page_position"] = item.get("position")
            row["position"] = absolute_position
            merged.append(row)

    return query_used, merged


def _matching_serp_items(
    items: list[dict],
    keyword: str,
) -> list[dict]:
    """Pick dataset items that belong to this keyword (all pages)."""
    key = _normalize_query_key(keyword)
    matched = []
    for item in items:
        if not isinstance(item, dict):
            continue
        search_query = item.get("searchQuery") or {}
        term = ""
        if isinstance(search_query, dict):
            term = str(search_query.get("term") or "").strip()
        if _normalize_query_key(term) == key:
            matched.append(item)

    # Fallback: if term matching fails, use all returned items (single-keyword run).
    if not matched:
        matched = [item for item in items if isinstance(item, dict)]
        if matched:
            logger.warning(
                "google_search: no exact term match for "
                f"keyword='{keyword}', using all {len(matched)} dataset item(s)"
            )
    return matched


def _scrape_keyword_once(
    *,
    keyword: str,
    website_url: str,
    country_code: str,
    call_counter: list[int] | None = None,
) -> dict:
    """One Apify run for one keyword (up to 2 Google pages)."""
    run_input = {
        "queries": keyword,
        "maxPagesPerQuery": MAX_PAGES_PER_QUERY,
        "countryCode": country_code.lower(),
        "languageCode": "en",
        "mobileResults": False,
        "saveHtml": False,
        "saveHtmlToKeyValueStore": False,
        "maximumLeadsEnrichmentRecords": 0,
        "focusOnPaidAds": False,
    }

    run = _run_actor_with_retry(
        GOOGLE_SEARCH_ACTOR,
        run_input,
        call_counter=call_counter,
    )
    items = list_actor_items(run)
    matching_items = _matching_serp_items(items, keyword)
    query_used, organic_results = _merge_organic_results_absolute(
        matching_items,
        fallback_keyword=keyword,
    )
    owned = _find_owned_organic_result(organic_results, website_url)

    return {
        "query": query_used,
        "organic_results": organic_results,
        "owned": owned,
        "dataset_item_count": len(items),
    }


def scrape_google_search_ranks(
    *,
    keywords: list[str],
    website_url: str,
    country_code: str,
) -> list[dict]:
    """
    For each keyword, scrape Google up to KEYWORD_SCRAPE_ATTEMPTS times
    (2 pages each). Found if present in any attempt; keep best position.
    """
    cleaned_keywords: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        text = (keyword or "").strip()
        if not text:
            continue
        key = _normalize_query_key(text)
        if key in seen:
            continue
        seen.add(key)
        cleaned_keywords.append(text)

    if not cleaned_keywords:
        raise ValueError("At least one keyword is required")

    cleaned_website_url = clean_url(website_url)
    owned_host = normalize_host(cleaned_website_url)
    if not cleaned_website_url or not owned_host:
        raise ValueError(f"Invalid website_url: {website_url}")

    logger.info(
        "google_search: cleaned website_url "
        f"'{website_url}' → '{cleaned_website_url}' (host='{owned_host}')"
    )

    results: list[dict] = []
    total_actor_runs = 0

    for keyword in cleaned_keywords:
        best_owned: dict | None = None
        best_organics: list[dict] = []
        query_used = keyword
        attempts_used = 0
        last_error: Exception | None = None
        call_counter: list[int] = []

        logger.info(
            f"google_search: keyword='{keyword}' — starting up to "
            f"{KEYWORD_SCRAPE_ATTEMPTS} attempt(s), "
            f"max_pages={MAX_PAGES_PER_QUERY}"
        )

        for attempt in range(1, KEYWORD_SCRAPE_ATTEMPTS + 1):
            attempts_used = attempt
            try:
                logger.info(
                    f"google_search: keyword='{keyword}' "
                    f"attempt {attempt}/{KEYWORD_SCRAPE_ATTEMPTS}"
                )
                once = _scrape_keyword_once(
                    keyword=keyword,
                    website_url=cleaned_website_url,
                    country_code=country_code,
                    call_counter=call_counter,
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    f"google_search: keyword='{keyword}' "
                    f"attempt {attempt} failed — {exc}"
                )
                if attempt < KEYWORD_SCRAPE_ATTEMPTS:
                    time.sleep(RETRY_DELAY_SECONDS)
                continue

            query_used = once["query"] or keyword
            organics = once["organic_results"] or []
            owned = once["owned"]

            logger.info(
                f"google_search: keyword='{keyword}' attempt {attempt} — "
                f"dataset_items={once['dataset_item_count']}, "
                f"organic_count={len(organics)}, "
                f"found={owned is not None}"
                + (
                    f", position={owned['position']}"
                    if owned
                    else ""
                )
                + f", actor_runs_so_far={len(call_counter)}"
            )

            if owned is not None:
                if best_owned is None or owned["position"] < best_owned["position"]:
                    best_owned = owned
                    best_organics = organics

            # Keep last organics if never found, so webhook still has SERP data.
            if best_owned is None:
                best_organics = organics

            if attempt < KEYWORD_SCRAPE_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)

        keyword_actor_runs = len(call_counter)
        total_actor_runs += keyword_actor_runs

        if best_owned is None and last_error is not None and not best_organics:
            logger.info(
                f"google_search: keyword='{keyword}' — actor ran "
                f"{keyword_actor_runs} time(s) before failing"
            )
            raise last_error

        visibility = score_search_visibility(
            best_owned["position"] if best_owned else None
        )

        if best_owned:
            logger.info(
                f"google_search: keyword='{keyword}' — FOUND after "
                f"{attempts_used} attempt(s), best_position={best_owned['position']}, "
                f"url='{best_owned['url']}', actor_runs={keyword_actor_runs}"
            )
        else:
            logger.info(
                f"google_search: keyword='{keyword}' — NOT FOUND after "
                f"{attempts_used} attempt(s), "
                f"organic_count={len(best_organics)}, "
                f"actor_runs={keyword_actor_runs}"
            )

        results.append(
            {
                "keyword": keyword,
                "query": query_used,
                "organic_count": len(best_organics),
                "organic_results": best_organics,
                "scrape_attempts": attempts_used,
                "actor_runs": keyword_actor_runs,
                "found": best_owned is not None,
                "position": best_owned["position"] if best_owned else None,
                "url": best_owned["url"] if best_owned else None,
                "title": best_owned["title"] if best_owned else None,
                "displayed_url": best_owned["displayed_url"] if best_owned else None,
                "score": visibility["score"],
                "max_score": visibility["max_score"],
                "band": visibility["band"],
            }
        )

    logger.info(
        f"google_search: finished — total_actor_runs={total_actor_runs} "
        f"across {len(results)} keyword(s)"
    )
    return results
