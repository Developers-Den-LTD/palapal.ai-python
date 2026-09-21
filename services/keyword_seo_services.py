"""
Custom Keyword SEO Analysis.

Step 1 (current): Google search visibility via Apify.

All keywords are scraped together in 2 batched Apify runs (parallel).
Each keyword still gets 2 scrapes and 2 Google pages.
Priority is metadata only — used to break ties when picking matched_keyword.
"""

from schema.keyword_seo_schema import KeywordItem, KeywordSeoRequest
from services.google_search_services import clean_url, scrape_google_search_ranks
from services.logger_services import logger

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _normalize_keyword(text: str) -> str:
    return " ".join((text or "").lower().split())


def _pick_matched_keyword(attempts: list[dict]) -> dict | None:
    """
    Prefer the best found result (lowest position).
    On a position tie, prefer higher priority, then earlier payload order.
    If none found, return the first attempt (stable fallback for consumers).
    """
    if not attempts:
        return None

    found = [item for item in attempts if item.get("found")]
    if not found:
        return attempts[0]

    def _sort_key(item: dict) -> tuple:
        position = item.get("position")
        return (
            int(position) if position is not None else 10**9,
            PRIORITY_ORDER.get(str(item.get("priority") or "").lower(), 99),
            int(item.get("payload_index") or 0),
        )

    return min(found, key=_sort_key)


def _google_by_keyword(google_results: list[dict]) -> dict[str, dict]:
    """Map normalized keyword text → google scrape result."""
    mapped: dict[str, dict] = {}
    for result in google_results:
        key = _normalize_keyword(str(result.get("keyword") or ""))
        if key and key not in mapped:
            mapped[key] = result
    return mapped


def run_keyword_seo_analysis(payload: KeywordSeoRequest) -> dict:
    website_url_raw = payload.website_url.strip()
    website_url = clean_url(website_url_raw)
    country_code = payload.country_code.strip().lower()

    if not website_url:
        raise ValueError(f"Invalid website_url: {website_url_raw}")

    keywords: list[KeywordItem] = list(payload.keywords)

    logger.info(
        "keyword_seo: started — "
        f"website_raw='{website_url_raw}', website_cleaned='{website_url}', "
        f"country_code='{country_code}', keywords={len(keywords)}, "
        f"parallel=True, "
        f"keywords={[f'{k.priority}:{k.keyword}' for k in keywords]}"
    )

    keyword_texts = [item.keyword for item in keywords]
    google_results = scrape_google_search_ranks(
        keywords=keyword_texts,
        website_url=website_url,
        country_code=country_code,
    )
    google_map = _google_by_keyword(google_results)
    # Prefer real Apify batch call count (usually 2), not per-keyword sums.
    if google_results and google_results[0].get("batch_actor_runs") is not None:
        total_actor_runs = int(google_results[0]["batch_actor_runs"] or 0)
    else:
        total_actor_runs = sum(
            int(item.get("actor_runs") or 0) for item in google_results
        )

    attempts: list[dict] = []

    for index, item in enumerate(keywords):
        google = google_map.get(_normalize_keyword(item.keyword))

        if not google:
            attempt = {
                "keyword": item.keyword,
                "priority": item.priority,
                "payload_index": index,
                "found": False,
                "position": None,
                "url": None,
                "title": None,
                "score": 0,
                "max_score": 15,
                "band": "missing",
                "scrape_attempts": 0,
                "actor_runs": 0,
                "organic_results": [],
            }
            attempts.append(attempt)
            continue

        keyword_actor_runs = int(google.get("actor_runs") or 0)

        attempt = {
            "keyword": item.keyword,
            "priority": item.priority,
            "payload_index": index,
            "found": google["found"],
            "position": google["position"],
            "url": google["url"],
            "title": google["title"],
            "score": google["score"],
            "max_score": google["max_score"],
            "band": google["band"],
            "scrape_attempts": google.get("scrape_attempts") or 0,
            "actor_runs": keyword_actor_runs,
            "organic_results": google.get("organic_results") or [],
        }
        attempts.append(attempt)

        logger.info(
            "keyword_seo: keyword result — "
            f"priority='{item.priority}', keyword='{item.keyword}', "
            f"found={attempt['found']}, position={attempt['position']}, "
            f"actor_runs={keyword_actor_runs}"
        )

    matched = _pick_matched_keyword(attempts)

    # Drop internal sort helper from webhook payload.
    public_attempts = [
        {k: v for k, v in attempt.items() if k != "payload_index"}
        for attempt in attempts
    ]
    public_matched = None
    if matched is not None:
        public_matched = {
            k: v for k, v in matched.items() if k != "payload_index"
        }

    logger.info(
        "keyword_seo: completed — "
        f"found={bool(public_matched and public_matched.get('found'))}, "
        f"keywords_tried={len(public_attempts)}, "
        f"total_actor_runs={total_actor_runs}, "
        f"matched_keyword="
        f"'{public_matched.get('keyword') if public_matched else None}'"
    )

    return {
        "status": "success",
        "website_url": website_url,
        "country_code": country_code,
        "found": bool(public_matched and public_matched.get("found")),
        "total_actor_runs": total_actor_runs,
        "matched_keyword": public_matched,
        "attempts": public_attempts,
    }
