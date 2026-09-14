"""
Custom Keyword SEO Analysis.

Step 1 (current): Google search visibility via Apify.

Keywords are tried by priority: high → medium → low.
Within the same priority, payload order is kept.
Stops at the first keyword where website_url is found in organic results.
"""

from schema.keyword_seo_schema import KeywordItem, KeywordSeoRequest
from services.google_search_services import clean_url, scrape_google_search_ranks
from services.logger_services import logger

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _keywords_by_priority(keywords: list[KeywordItem]) -> list[KeywordItem]:
    """high first, then medium, then low; keep original order within each priority."""
    indexed = list(enumerate(keywords))
    indexed.sort(key=lambda pair: (PRIORITY_ORDER[pair[1].priority], pair[0]))
    return [item for _, item in indexed]


def run_keyword_seo_analysis(payload: KeywordSeoRequest) -> dict:
    website_url_raw = payload.website_url.strip()
    website_url = clean_url(website_url_raw)
    country_code = payload.country_code.strip().lower()

    if not website_url:
        raise ValueError(f"Invalid website_url: {website_url_raw}")

    ordered_keywords = _keywords_by_priority(payload.keywords)

    logger.info(
        "keyword_seo: started — "
        f"website_raw='{website_url_raw}', website_cleaned='{website_url}', "
        f"country_code='{country_code}', keywords={len(ordered_keywords)}, "
        f"order={[f'{k.priority}:{k.keyword}' for k in ordered_keywords]}"
    )

    attempts: list[dict] = []
    matched: dict | None = None
    total_actor_runs = 0

    for item in ordered_keywords:
        logger.info(
            "keyword_seo: trying keyword "
            f"priority='{item.priority}', keyword='{item.keyword}'"
        )

        google_results = scrape_google_search_ranks(
            keywords=[item.keyword],
            website_url=website_url,
            country_code=country_code,
        )
        google = google_results[0] if google_results else None

        if not google:
            attempt = {
                "keyword": item.keyword,
                "priority": item.priority,
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
        total_actor_runs += keyword_actor_runs

        attempt = {
            "keyword": item.keyword,
            "priority": item.priority,
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

        if google["found"]:
            matched = attempt
            logger.info(
                "keyword_seo: website found — stopping. "
                f"priority='{item.priority}', keyword='{item.keyword}', "
                f"position={google['position']}, "
                f"actor_runs_for_keyword={keyword_actor_runs}, "
                f"total_actor_runs_so_far={total_actor_runs}"
            )
            break

        logger.info(
            "keyword_seo: website not found for "
            f"priority='{item.priority}', keyword='{item.keyword}' — "
            f"actor_runs_for_keyword={keyword_actor_runs}, "
            f"total_actor_runs_so_far={total_actor_runs} — trying next"
        )

    if matched is None and attempts:
        matched = attempts[-1]
        logger.info(
            "keyword_seo: website not found for any keyword — "
            f"returning last attempt keyword='{matched['keyword']}'"
        )

    logger.info(
        "keyword_seo: completed — "
        f"found={bool(matched and matched.get('found'))}, "
        f"keywords_tried={len(attempts)}, "
        f"total_actor_runs={total_actor_runs}"
    )

    return {
        "status": "success",
        "website_url": website_url,
        "country_code": country_code,
        "found": bool(matched and matched.get("found")),
        "total_actor_runs": total_actor_runs,
        "matched_keyword": matched,
        "attempts": attempts,
    }
