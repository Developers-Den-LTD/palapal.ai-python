import re

from ddgs import DDGS

from schema.url_finder import BusinessSearchRequest
from services.logger_services import logger

_STOPWORDS = {"the", "and", "of", "a", "an", "by", "restaurant", "hotel", "cafe", "bar"}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _significant_words(text: str) -> list[str]:
    return [
        w
        for w in re.findall(r"[a-zA-Z0-9]+", text.lower())
        if w not in _STOPWORDS and len(w) > 1
    ]


def is_valid_yelp(url: str) -> bool:
    # Accept international Yelp domains too (e.g. yelp.co.uk, yelp.ca) as long as it's a business page.
    return bool(re.search(r"yelp\.[a-z.]+/biz/", url, flags=re.IGNORECASE))


def is_valid_tripadvisor(url: str) -> bool:
    return (
        "tripadvisor.com/Restaurant_Review" in url
        or "tripadvisor.com/Hotel_Review" in url
        or "tripadvisor.com/Attraction_Review" in url
    )


def _match_score(text: str, business_name: str) -> tuple[int, int]:
    """Return (matches, total_words) for significant business-name words found in text."""
    name_words = _significant_words(business_name)
    if not name_words:
        return 0, 0

    normalized_text = _normalize(text)
    matches = sum(1 for w in name_words if _normalize(w) and _normalize(w) in normalized_text)
    return matches, len(name_words)


def _normalize_yelp_url(url: str) -> str:
    return url.replace("://m.yelp.com/", "://www.yelp.com/")


def result_matches_business(*, url: str, business_name: str) -> bool:
    """Loose match: require "some" overlap, not a perfect URL-slug match."""
    matches, total = _match_score(url, business_name)
    if total == 0:
        return False

    # If the business name has many words, Yelp/TA slugs often omit some of them.
    # So accept if at least 2 significant words match, OR at least ~half match.
    return matches >= 2 or matches >= max(1, (total + 1) // 2)


def _extract_platform_urls(
    results,
    *,
    business_name: str,
    need_yelp: bool = True,
    need_tripadvisor: bool = True,
) -> tuple[str | None, str | None]:
    yelp_url = None
    tripadvisor_url = None

    # Fallback candidates when strict matching fails (still platform-valid).
    yelp_candidate = None
    tripadvisor_candidate = None

    for r in results:
        url = r.get("href", "") or ""
        title = r.get("title", "") or ""
        body = r.get("body", "") or ""
        match_text = f"{url} {title} {body}"

        if need_yelp and yelp_candidate is None and is_valid_yelp(url):
            yelp_candidate = _normalize_yelp_url(url)

        if need_tripadvisor and tripadvisor_candidate is None and is_valid_tripadvisor(url):
            tripadvisor_candidate = url

        if not result_matches_business(url=match_text, business_name=business_name):
            continue

        if need_yelp and yelp_url is None and is_valid_yelp(url):
            yelp_url = _normalize_yelp_url(url)
            logger.info(f"find_business_links: found Yelp URL={yelp_url}")

        if need_tripadvisor and tripadvisor_url is None and is_valid_tripadvisor(url):
            tripadvisor_url = url
            logger.info(f"find_business_links: found TripAdvisor URL={tripadvisor_url}")

        if (not need_yelp or yelp_url) and (not need_tripadvisor or tripadvisor_url):
            break

    # If we didn't get a confident match, return best platform-valid candidates.
    return yelp_url or yelp_candidate, tripadvisor_url or tripadvisor_candidate


def find_business_links(payload: BusinessSearchRequest) -> dict:
    logger.info(
        f"find_business_links: request received business='{payload.business_name}', "
        f"location='{payload.location}', exact_place='{payload.exact_place}'"
    )

    query_components = [payload.business_name]
    if payload.exact_place:
        query_components.append(payload.exact_place)
    query_components.append(payload.location)
    query_components.append("yelp tripadvisor")

    search_query = " ".join(query_components)
    logger.info(f"find_business_links: search query='{search_query}'")

    with DDGS() as ddgs:
        results = ddgs.text(search_query, max_results=40)
        yelp_url, tripadvisor_url = _extract_platform_urls(
            results,
            business_name=payload.business_name,
        )

        if yelp_url is None:
            yelp_query = f'"{payload.business_name}" {payload.location} site:yelp.com'
            logger.info(f"find_business_links: yelp fallback query='{yelp_query}'")
            yelp_results = ddgs.text(yelp_query, max_results=20)
            yelp_url, _ = _extract_platform_urls(
                yelp_results,
                business_name=payload.business_name,
                need_yelp=True,
                need_tripadvisor=False,
            )

        if tripadvisor_url is None:
            tripadvisor_query = (
                f'"{payload.business_name}" {payload.location} site:tripadvisor.com'
            )
            logger.info(
                f"find_business_links: tripadvisor fallback query='{tripadvisor_query}'"
            )
            tripadvisor_results = ddgs.text(tripadvisor_query, max_results=20)
            _, tripadvisor_url = _extract_platform_urls(
                tripadvisor_results,
                business_name=payload.business_name,
                need_yelp=False,
                need_tripadvisor=True,
            )

    if not yelp_url and not tripadvisor_url:
        logger.warning(
            f"find_business_links: no business found for name='{payload.business_name}', "
            f"query='{search_query}'"
        )
        return {
            "status": "not_found",
            "message": f"No business exists with this name: {payload.business_name}",
            "search_query": search_query,
            "results": {
                "yelp": None,
                "tripadvisor": None,
            },
        }

    logger.info(
        f"find_business_links: success yelp={yelp_url}, tripadvisor={tripadvisor_url}"
    )

    return {
        "status": "success",
        "search_query": search_query,
        "results": {
            "yelp": yelp_url,
            "tripadvisor": tripadvisor_url,
        },
    }
