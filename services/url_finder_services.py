import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

from schema.url_finder import BusinessSearchRequest, WebsiteCrawlRequest
from services.logger_services import logger
from utils.platform_urls import normalize_tripadvisor_url, normalize_yelp_url

_CRAWL_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_CRAWL_HEADERS = {"User-Agent": _CRAWL_USER_AGENT}
_CRAWL_TIMEOUT = 10.0
_DEFAULT_MAX_WORKERS = 8
_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_COMMON_SITEMAP_PATHS = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/wp-sitemap.xml",
)

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
    return bool(
        re.search(
            r"tripadvisor\.[a-z.]+/(Restaurant_Review|Hotel_Review|Attraction_Review)",
            url,
            flags=re.IGNORECASE,
        )
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
    return normalize_yelp_url(url) or url


def _normalize_tripadvisor_url(url: str) -> str:
    return normalize_tripadvisor_url(url) or url

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
            tripadvisor_candidate = _normalize_tripadvisor_url(url)

        if not result_matches_business(url=match_text, business_name=business_name):
            continue

        if need_yelp and yelp_url is None and is_valid_yelp(url):
            yelp_url = _normalize_yelp_url(url)
            logger.info(f"find_business_links: found Yelp URL={yelp_url}")

        if need_tripadvisor and tripadvisor_url is None and is_valid_tripadvisor(url):
            tripadvisor_url = _normalize_tripadvisor_url(url)
            logger.info(f"find_business_links: found TripAdvisor URL={tripadvisor_url}")

        if (not need_yelp or yelp_url) and (not need_tripadvisor or tripadvisor_url):
            break

    # If we didn't get a confident match, return best platform-valid candidates.
    return yelp_url or yelp_candidate, tripadvisor_url or tripadvisor_candidate


def _normalize_crawl_start_url(start_url: str) -> str:
    """Ensure start URL has a scheme and is valid."""
    start_url = start_url.strip()
    parsed = urlparse(start_url)
    if not parsed.scheme:
        start_url = f"https://{start_url}"
        parsed = urlparse(start_url)
    if not parsed.netloc:
        raise ValueError(f"Invalid start URL: {start_url}")
    return start_url


def _same_crawl_domain(url: str, domain: str) -> bool:
    """Check if URL belongs to the same site (handles www vs non-www)."""
    netloc = urlparse(url).netloc.lower()
    base = domain.lower()
    if netloc == base:
        return True
    if netloc.startswith("www.") and netloc[4:] == base:
        return True
    if base.startswith("www.") and netloc == base[4:]:
        return True
    return False


def _normalize_crawl_link(href: str, base_url: str) -> str | None:
    """Turn a link into a clean absolute URL, or None if it should be skipped."""
    if not href or not str(href).strip():
        return None

    href = str(href).strip()
    if href.startswith(("mailto:", "tel:", "javascript:", "data:")):
        return None

    full_url = urljoin(base_url, href)
    full_url = full_url.split("#")[0].strip()
    if not full_url:
        return None

    parsed = urlparse(full_url)
    if parsed.scheme not in ("http", "https"):
        return None

    return urlunparse((
        parsed.scheme,
        parsed.netloc.lower(),
        parsed.path or "/",
        "",
        "",
        "",
    ))


def _create_crawl_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(_CRAWL_HEADERS)
    return session


def _extract_links_from_html(html: str, page_url: str, domain: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    for link in soup.find_all("a", href=True):
        full_url = _normalize_crawl_link(link["href"], page_url)
        if not full_url:
            continue
        if not _same_crawl_domain(full_url, domain):
            continue
        links.append(full_url)
    return links


def _parse_sitemap_document(content: str) -> tuple[list[str], list[str]]:
    """Return (page URLs, nested sitemap URLs) from sitemap XML."""
    page_urls: list[str] = []
    sitemap_urls: list[str] = []

    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return page_urls, sitemap_urls

    for element in root.findall(".//sm:sitemap/sm:loc", _SITEMAP_NS):
        loc = (element.text or "").strip()
        if loc:
            sitemap_urls.append(loc)

    for element in root.findall(".//sm:url/sm:loc", _SITEMAP_NS):
        loc = (element.text or "").strip()
        if loc:
            page_urls.append(loc)

    if not page_urls and not sitemap_urls:
        for element in root.findall(".//{*}sitemap/{*}loc"):
            loc = (element.text or "").strip()
            if loc:
                sitemap_urls.append(loc)

        for element in root.findall(".//{*}url/{*}loc"):
            loc = (element.text or "").strip()
            if loc:
                page_urls.append(loc)

    return page_urls, sitemap_urls


def _fetch_sitemap_urls(
    session: requests.Session,
    sitemap_url: str,
    domain: str,
    *,
    max_pages: int,
    max_sitemaps: int = 10,
) -> list[str]:
    to_fetch = deque([sitemap_url])
    fetched_sitemaps: set[str] = set()
    discovered: list[str] = []
    seen_pages: set[str] = set()

    while to_fetch and len(fetched_sitemaps) < max_sitemaps and len(discovered) < max_pages:
        current = to_fetch.popleft()
        if current in fetched_sitemaps:
            continue
        fetched_sitemaps.add(current)

        try:
            response = session.get(current, timeout=_CRAWL_TIMEOUT)
            if response.status_code != 200:
                continue
            page_urls, nested_sitemaps = _parse_sitemap_document(response.text)
        except requests.RequestException as exc:
            logger.debug(f"crawl_website: sitemap fetch failed '{current}' — {exc}")
            continue

        for url in page_urls:
            normalized = _normalize_crawl_link(url, current)
            if not normalized or not _same_crawl_domain(normalized, domain):
                continue
            if normalized in seen_pages:
                continue
            seen_pages.add(normalized)
            discovered.append(normalized)
            if len(discovered) >= max_pages:
                break

        for nested in nested_sitemaps:
            if nested not in fetched_sitemaps:
                to_fetch.append(nested)

    return discovered


def _discover_urls_from_sitemap(
    start_url: str,
    max_pages: int,
    session: requests.Session | None = None,
) -> list[str]:
    """
    Fast path: read sitemap.xml (or robots.txt Sitemap entries) instead of crawling pages.
    """
    start_url = _normalize_crawl_start_url(start_url)
    domain = urlparse(start_url).netloc.lower()
    owns_session = session is None
    session = session or _create_crawl_session()

    sitemap_candidates: list[str] = []
    parsed = urlparse(start_url)
    base = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))

    for path in _COMMON_SITEMAP_PATHS:
        sitemap_candidates.append(urljoin(base, path))

    try:
        robots_url = urljoin(base, "/robots.txt")
        response = session.get(robots_url, timeout=_CRAWL_TIMEOUT)
        if response.status_code == 200:
            for line in response.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    sitemap_url = line.split(":", 1)[1].strip()
                    if sitemap_url:
                        sitemap_candidates.append(sitemap_url)
    except requests.RequestException:
        pass

    seen_candidates: set[str] = set()
    for sitemap_url in sitemap_candidates:
        if sitemap_url in seen_candidates:
            continue
        seen_candidates.add(sitemap_url)

        urls = _fetch_sitemap_urls(
            session,
            sitemap_url,
            domain,
            max_pages=max_pages,
        )
        if urls:
            logger.info(
                f"crawl_website: sitemap discovery found {len(urls)} URL(s) via '{sitemap_url}'"
            )
            if owns_session:
                session.close()
            return sorted(urls[:max_pages])

    if owns_session:
        session.close()
    return []


def _crawl_website_sequential(
    start_url: str,
    domain: str,
    max_pages: int,
    delay_seconds: float,
    session: requests.Session,
) -> list[str]:
    visited: set[str] = set()
    to_visit: list[str] = [start_url]

    while to_visit and len(visited) < max_pages:
        url = to_visit.pop(0)
        if url in visited:
            continue

        try:
            response = session.get(url, timeout=_CRAWL_TIMEOUT)
            if response.status_code != 200:
                logger.warning(
                    f"crawl_website: skipped '{url}' — HTTP {response.status_code}"
                )
                visited.add(url)
                continue

            visited.add(url)
            for full_url in _extract_links_from_html(response.text, url, domain):
                if full_url not in visited and full_url not in to_visit:
                    to_visit.append(full_url)

            if delay_seconds > 0:
                time.sleep(delay_seconds)

        except requests.RequestException as exc:
            logger.warning(f"crawl_website: failed '{url}' — {exc}")
            visited.add(url)
        except Exception as exc:
            logger.exception(f"crawl_website: unexpected error for '{url}' — {exc}")
            visited.add(url)

    return sorted(visited)


def _crawl_website_parallel(
    start_url: str,
    domain: str,
    max_pages: int,
    delay_seconds: float,
    max_workers: int,
    session: requests.Session,
) -> list[str]:
    visited: set[str] = set()
    in_flight: set[str] = set()
    to_visit: deque[str] = deque([start_url])
    lock = threading.Lock()

    def fetch_page(url: str) -> list[str]:
        try:
            response = session.get(url, timeout=_CRAWL_TIMEOUT)
            if response.status_code != 200:
                logger.warning(
                    f"crawl_website: skipped '{url}' — HTTP {response.status_code}"
                )
                return []
            return _extract_links_from_html(response.text, url, domain)
        except requests.RequestException as exc:
            logger.warning(f"crawl_website: failed '{url}' — {exc}")
            return []
        except Exception as exc:
            logger.exception(f"crawl_website: unexpected error for '{url}' — {exc}")
            return []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures: dict = {}

        while len(visited) < max_pages:
            with lock:
                while to_visit and len(futures) < max_workers and len(visited) < max_pages:
                    url = to_visit.popleft()
                    if url in visited or url in in_flight:
                        continue
                    in_flight.add(url)
                    futures[executor.submit(fetch_page, url)] = url

            if not futures:
                break

            done, _ = wait(futures.keys(), return_when=FIRST_COMPLETED)
            for future in done:
                url = futures.pop(future)
                new_links = future.result()

                with lock:
                    in_flight.discard(url)
                    visited.add(url)
                    for link in new_links:
                        if link not in visited and link not in in_flight and link not in to_visit:
                            to_visit.append(link)

            if delay_seconds > 0 and futures:
                time.sleep(delay_seconds)

    return sorted(visited)


def crawl_website(
    start_url: str,
    max_pages: int = 100,
    delay_seconds: float = 0.0,
    *,
    max_workers: int = _DEFAULT_MAX_WORKERS,
    prefer_sitemap: bool = True,
) -> tuple[list[str], str]:
    """
    Discover internal URLs for a website.

  Strategies (in order):
    1. sitemap.xml / robots.txt (very fast, usually 1-3 requests)
    2. parallel HTML crawl (multiple pages at once)
    3. sequential crawl when max_workers=1

    Returns (urls, discovery_method) where discovery_method is "sitemap" or "crawl".
    """
    start_url = _normalize_crawl_start_url(start_url)
    domain = urlparse(start_url).netloc.lower()

    logger.info(
        f"crawl_website: start_url='{start_url}', max_pages={max_pages}, "
        f"delay_seconds={delay_seconds}, max_workers={max_workers}, "
        f"prefer_sitemap={prefer_sitemap}"
    )

    if prefer_sitemap:
        sitemap_urls = _discover_urls_from_sitemap(start_url, max_pages)
        if sitemap_urls:
            logger.info(
                f"crawl_website: finished via sitemap — found {len(sitemap_urls)} page(s)"
            )
            return sitemap_urls, "sitemap"

    session = _create_crawl_session()
    try:
        if max_workers > 1:
            urls = _crawl_website_parallel(
                start_url,
                domain,
                max_pages,
                delay_seconds,
                max_workers,
                session,
            )
        else:
            urls = _crawl_website_sequential(
                start_url,
                domain,
                max_pages,
                delay_seconds,
                session,
            )
    finally:
        session.close()

    logger.info(f"crawl_website: finished via crawl — found {len(urls)} page(s)")
    return urls, "crawl"


def run_website_crawl(payload: WebsiteCrawlRequest) -> dict:
    """API wrapper: crawl a site and return structured results."""
    urls, discovery_method = crawl_website(
        payload.start_url,
        max_pages=payload.max_pages,
        delay_seconds=payload.delay_seconds,
        max_workers=payload.max_workers,
        prefer_sitemap=payload.prefer_sitemap,
    )
    start_url = _normalize_crawl_start_url(payload.start_url)

    return {
        "status": "success",
        "start_url": start_url,
        "domain": urlparse(start_url).netloc,
        "max_pages": payload.max_pages,
        "delay_seconds": payload.delay_seconds,
        "max_workers": payload.max_workers,
        "prefer_sitemap": payload.prefer_sitemap,
        "discovery_method": discovery_method,
        "url_count": len(urls),
        "urls": urls,
    }


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
