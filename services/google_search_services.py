"""
Google Search visibility via Apify apify/google-search-scraper.

For each keyword:
- scrape KEYWORD_SCRAPE_ATTEMPTS times
- use maxPagesPerQuery = 2
- if the site appears in any attempt → found
- keep the best (lowest absolute) position across attempts

Speed: all keywords are batched into KEYWORD_SCRAPE_ATTEMPTS Apify runs
(newline-separated queries). Those batch runs execute in parallel.
Example: 6 keywords × 2 attempts → 2 actor calls (not 12).
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urlunparse

from services.logger_services import logger
from services.scrapper_services import _run_actor_with_retry
from services.social_media_scrapper.common import list_actor_items

GOOGLE_SEARCH_ACTOR = "apify/google-search-scraper"
MAX_SEARCH_VISIBILITY_SCORE = 15
KEYWORD_SCRAPE_ATTEMPTS = 2
MAX_PAGES_PER_QUERY = 2

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
    *,
    allow_all_fallback: bool = True,
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

    # Fallback only for single-keyword runs. Never reuse the full dataset
    # when multiple keywords were scraped together.
    if not matched and allow_all_fallback:
        matched = [item for item in items if isinstance(item, dict)]
        if matched:
            logger.warning(
                "google_search: no exact term match for "
                f"keyword='{keyword}', using all {len(matched)} dataset item(s)"
            )
    return matched


def _parse_batch_items_for_keywords(
    *,
    items: list[dict],
    keywords: list[str],
    website_url: str,
) -> dict[str, dict]:
    """Split one batched Apify dataset into per-keyword scrape results."""
    multi_keyword = len(keywords) > 1
    by_keyword: dict[str, dict] = {}

    for keyword in keywords:
        matching_items = _matching_serp_items(
            items,
            keyword,
            allow_all_fallback=not multi_keyword,
        )
        query_used, organic_results = _merge_organic_results_absolute(
            matching_items,
            fallback_keyword=keyword,
        )
        owned = _find_owned_organic_result(organic_results, website_url)
        by_keyword[keyword] = {
            "query": query_used,
            "organic_results": organic_results,
            "owned": owned,
            "dataset_item_count": len(matching_items),
        }

    return by_keyword


def _scrape_keywords_batch_once(
    *,
    keywords: list[str],
    website_url: str,
    country_code: str,
    attempt: int,
    call_counter: list[int] | None = None,
) -> dict:
    """
    One Apify run for ALL keywords (newline-separated queries).

    Keeps maxPagesPerQuery = 2 for every keyword inside this single run.
    """
    queries = "\n".join(keywords)
    run_input = {
        "queries": queries,
        "maxPagesPerQuery": MAX_PAGES_PER_QUERY,
        "countryCode": country_code.lower(),
        "languageCode": "en",
        "mobileResults": False,
        "saveHtml": False,
        "saveHtmlToKeyValueStore": False,
        "maximumLeadsEnrichmentRecords": 0,
        "focusOnPaidAds": False,
        # Let Apify scrape multiple queries concurrently inside this one run.
        "maxConcurrency": min(10, max(1, len(keywords))),
    }

    logger.info(
        f"google_search: batch attempt {attempt}/{KEYWORD_SCRAPE_ATTEMPTS} — "
        f"starting actor with {len(keywords)} keyword(s), "
        f"max_pages={MAX_PAGES_PER_QUERY}, "
        f"maxConcurrency={run_input['maxConcurrency']}"
    )

    run = _run_actor_with_retry(
        GOOGLE_SEARCH_ACTOR,
        run_input,
        call_counter=call_counter,
    )
    items = list_actor_items(run)
    by_keyword = _parse_batch_items_for_keywords(
        items=items,
        keywords=keywords,
        website_url=website_url,
    )

    logger.info(
        f"google_search: batch attempt {attempt}/{KEYWORD_SCRAPE_ATTEMPTS} — "
        f"dataset_items={len(items)}, keywords_parsed={len(by_keyword)}"
    )

    return {
        "by_keyword": by_keyword,
        "dataset_item_count": len(items),
    }


def _merge_keyword_attempt_results(
    *,
    keyword: str,
    attempt_outcomes: list[dict],
) -> dict:
    """
    Merge parallel scrape attempts for one keyword.

    Found if present in any successful attempt; keep best (lowest) position.
    If every attempt failed with no SERP data, return a missing-band result
    that includes an error string (caller may raise if all keywords hard-fail).
    """
    best_owned: dict | None = None
    best_organics: list[dict] = []
    query_used = keyword
    last_error: Exception | None = None
    total_actor_runs = 0
    successful_attempts = 0

    for outcome in attempt_outcomes:
        total_actor_runs += int(outcome.get("actor_runs") or 0)
        error = outcome.get("error")
        if error is not None:
            last_error = error
            continue

        successful_attempts += 1
        once = outcome["result"]
        query_used = once["query"] or keyword
        organics = once["organic_results"] or []
        owned = once["owned"]

        logger.info(
            f"google_search: keyword='{keyword}' "
            f"attempt {outcome['attempt']}/{KEYWORD_SCRAPE_ATTEMPTS} — "
            f"dataset_items={once['dataset_item_count']}, "
            f"organic_count={len(organics)}, "
            f"found={owned is not None}"
            + (f", position={owned['position']}" if owned else "")
            + f", actor_runs={outcome.get('actor_runs') or 0}"
        )

        if owned is not None:
            if best_owned is None or owned["position"] < best_owned["position"]:
                best_owned = owned
                best_organics = organics

        # Keep last successful organics if never found, so webhook still has SERP data.
        if best_owned is None:
            best_organics = organics

    attempts_used = len(attempt_outcomes)
    hard_failed = (
        best_owned is None
        and last_error is not None
        and not best_organics
        and successful_attempts == 0
    )

    if hard_failed:
        logger.info(
            f"google_search: keyword='{keyword}' — actor ran "
            f"{total_actor_runs} time(s) before failing"
        )
        return {
            "keyword": keyword,
            "query": query_used,
            "organic_count": 0,
            "organic_results": [],
            "scrape_attempts": attempts_used,
            "actor_runs": total_actor_runs,
            "found": False,
            "position": None,
            "url": None,
            "title": None,
            "displayed_url": None,
            "score": 0,
            "max_score": MAX_SEARCH_VISIBILITY_SCORE,
            "band": "missing",
            "error": str(last_error),
        }

    visibility = score_search_visibility(
        best_owned["position"] if best_owned else None
    )

    if best_owned:
        logger.info(
            f"google_search: keyword='{keyword}' — FOUND after "
            f"{successful_attempts}/{attempts_used} successful attempt(s), "
            f"best_position={best_owned['position']}, "
            f"url='{best_owned['url']}', actor_runs={total_actor_runs}"
        )
    else:
        logger.info(
            f"google_search: keyword='{keyword}' — NOT FOUND after "
            f"{successful_attempts}/{attempts_used} successful attempt(s), "
            f"organic_count={len(best_organics)}, "
            f"actor_runs={total_actor_runs}"
        )

    return {
        "keyword": keyword,
        "query": query_used,
        "organic_count": len(best_organics),
        "organic_results": best_organics,
        "scrape_attempts": attempts_used,
        "actor_runs": total_actor_runs,
        "found": best_owned is not None,
        "position": best_owned["position"] if best_owned else None,
        "url": best_owned["url"] if best_owned else None,
        "title": best_owned["title"] if best_owned else None,
        "displayed_url": best_owned["displayed_url"] if best_owned else None,
        "score": visibility["score"],
        "max_score": visibility["max_score"],
        "band": visibility["band"],
    }


def scrape_google_search_ranks(
    *,
    keywords: list[str],
    website_url: str,
    country_code: str,
) -> list[dict]:
    """
    For each keyword, scrape Google KEYWORD_SCRAPE_ATTEMPTS times (2 pages each).

    All keywords are sent together in each Apify run (newline-separated).
    KEYWORD_SCRAPE_ATTEMPTS batch runs execute in parallel.
    Found if present in any attempt; keep best position per keyword.
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

    logger.info(
        "google_search: starting batched scrapes — "
        f"keywords={len(cleaned_keywords)}, "
        f"batch_runs={KEYWORD_SCRAPE_ATTEMPTS}, "
        f"max_pages={MAX_PAGES_PER_QUERY}, "
        f"mode=all_keywords_per_run"
    )

    def _run_batch_attempt(attempt: int) -> dict:
        call_counter: list[int] = []
        try:
            result = _scrape_keywords_batch_once(
                keywords=cleaned_keywords,
                website_url=cleaned_website_url,
                country_code=country_code,
                attempt=attempt,
                call_counter=call_counter,
            )
            return {
                "attempt": attempt,
                "by_keyword": result["by_keyword"],
                "dataset_item_count": result["dataset_item_count"],
                "error": None,
                "actor_runs": len(call_counter),
            }
        except Exception as exc:
            logger.warning(
                f"google_search: batch attempt {attempt}/"
                f"{KEYWORD_SCRAPE_ATTEMPTS} failed — {exc}"
            )
            return {
                "attempt": attempt,
                "by_keyword": {},
                "dataset_item_count": 0,
                "error": exc,
                "actor_runs": len(call_counter),
            }

    batch_outcomes: list[dict] = []
    with ThreadPoolExecutor(max_workers=KEYWORD_SCRAPE_ATTEMPTS) as executor:
        futures = [
            executor.submit(_run_batch_attempt, attempt)
            for attempt in range(1, KEYWORD_SCRAPE_ATTEMPTS + 1)
        ]
        for future in as_completed(futures):
            batch_outcomes.append(future.result())

    batch_outcomes.sort(key=lambda item: int(item["attempt"]))
    batch_actor_runs = sum(int(item.get("actor_runs") or 0) for item in batch_outcomes)

    # Convert batch outcomes → per-keyword attempt outcomes for existing merge logic.
    outcomes_by_keyword: dict[str, list[dict]] = {
        keyword: [] for keyword in cleaned_keywords
    }
    for batch in batch_outcomes:
        attempt = int(batch["attempt"])
        batch_error = batch.get("error")
        # One Apify call covered every keyword in this attempt.
        per_keyword_actor_share = 1 if int(batch.get("actor_runs") or 0) > 0 else 0

        if batch_error is not None:
            for keyword in cleaned_keywords:
                outcomes_by_keyword[keyword].append(
                    {
                        "keyword": keyword,
                        "attempt": attempt,
                        "result": None,
                        "error": batch_error,
                        "actor_runs": per_keyword_actor_share,
                    }
                )
            continue

        by_keyword = batch.get("by_keyword") or {}
        for keyword in cleaned_keywords:
            kw_once = by_keyword.get(keyword)
            if not kw_once:
                outcomes_by_keyword[keyword].append(
                    {
                        "keyword": keyword,
                        "attempt": attempt,
                        "result": {
                            "query": keyword,
                            "organic_results": [],
                            "owned": None,
                            "dataset_item_count": 0,
                        },
                        "error": None,
                        "actor_runs": per_keyword_actor_share,
                    }
                )
                continue

            outcomes_by_keyword[keyword].append(
                {
                    "keyword": keyword,
                    "attempt": attempt,
                    "result": kw_once,
                    "error": None,
                    "actor_runs": per_keyword_actor_share,
                }
            )

    results: list[dict] = []
    hard_failures: list[str] = []

    for keyword in cleaned_keywords:
        attempt_outcomes = sorted(
            outcomes_by_keyword[keyword],
            key=lambda item: int(item["attempt"]),
        )
        merged = _merge_keyword_attempt_results(
            keyword=keyword,
            attempt_outcomes=attempt_outcomes,
        )
        # Actual Apify calls for the whole job (shared across keywords).
        merged["batch_actor_runs"] = batch_actor_runs
        if merged.get("error") and not merged.get("organic_results"):
            hard_failures.append(keyword)
        results.append(merged)

    if hard_failures and len(hard_failures) == len(results):
        details = "; ".join(
            f"{item['keyword']}: {item.get('error')}" for item in results
        )
        raise RuntimeError(
            "Google search failed for all keywords — " + details
        )

    logger.info(
        f"google_search: finished — batch_actor_runs={batch_actor_runs} "
        f"across {len(results)} keyword(s)"
        + (f", hard_failures={hard_failures}" if hard_failures else "")
    )
    return results
