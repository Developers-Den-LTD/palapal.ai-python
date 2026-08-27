"""
Multi-page audit using Google PageSpeed (mobile + desktop).

Max 5 PageSpeed calls at once (asyncio.Semaphore).
Retries timeouts, connection drops, and HTTP 500s.
"""

from __future__ import annotations

import asyncio
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from core.config import settings
from services.logger_services import logger

PAGESPEED_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
MAX_WORKERS = 5
PAGESPEED_TIMEOUT_SECONDS = 180.0
PAGESPEED_MAX_ATTEMPTS = 3
PAGESPEED_RETRY_BASE_DELAY_SECONDS = 2.0
LCP_SLOW_SECONDS = 2.5
STRATEGIES = ("mobile", "desktop")
HTML_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
MAX_INTERNAL_LINKS_TO_CHECK = 40
# Transient failures worth retrying (timeouts, dropped connections, etc.).
_PAGESPEED_RETRY_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)


def _normalize_page_url(url: str) -> tuple[str, str, str]:
    """Returns (full_url, path, base_url)."""
    parsed = urlparse(str(url).strip())
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")

    path = parsed.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"

    base = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    full = f"{base}{path}{query}"
    return full, path, base


def _score_100(category: dict | None) -> float | None:
    if not category:
        return None
    raw = category.get("score")
    if raw is None:
        return None
    return round(float(raw) * 100, 1)


def _parse_lcp_seconds(numeric_value=None, display_value=None) -> float | None:
    if numeric_value is not None:
        try:
            return round(float(numeric_value) / 1000.0, 2)
        except (TypeError, ValueError):
            pass
    if display_value:
        try:
            return float(str(display_value).strip().split()[0])
        except (ValueError, IndexError):
            return None
    return None


def _empty_ps_result(error: str) -> dict:
    return {
        "ok": False,
        "error": error,
        "performance": None,
        "seo": None,
        "accessibility": None,
        "best_practices": None,
        "lcp": None,
        "missing_meta": False,
        "missing_alt_count": 0,
        "issues": [error],
    }


def _parse_pagespeed_payload(data: dict) -> dict:
    lighthouse = data.get("lighthouseResult", {})
    audits = lighthouse.get("audits", {})
    categories = lighthouse.get("categories", {})

    lcp_audit = audits.get("largest-contentful-paint", {})
    lcp = _parse_lcp_seconds(
        numeric_value=lcp_audit.get("numericValue"),
        display_value=lcp_audit.get("displayValue"),
    )

    missing_meta = audits.get("meta-description", {}).get("score") == 0
    image_alt = audits.get("image-alt", {})
    missing_alt_count = 0
    if image_alt.get("score") == 0:
        items = (image_alt.get("details") or {}).get("items") or []
        missing_alt_count = len(items)

    issues: list[str] = []
    if lcp is not None and lcp > LCP_SLOW_SECONDS:
        issues.append(f"Slow LCP ({lcp}s)")
    if missing_meta:
        issues.append("Missing meta description")
    if missing_alt_count:
        issues.append(f"{missing_alt_count} images missing alt text")

    return {
        "ok": True,
        "error": None,
        "performance": _score_100(categories.get("performance")),
        "seo": _score_100(categories.get("seo")),
        "accessibility": _score_100(categories.get("accessibility")),
        "best_practices": _score_100(categories.get("best-practices")),
        "lcp": lcp,
        "missing_meta": missing_meta,
        "missing_alt_count": missing_alt_count,
        "issues": issues,
    }


def _pagespeed_request(url: str, strategy: str) -> dict:
    """
    Call Google PageSpeed with retries for:
    - HTTP 500
    - read/connect timeouts
    - dropped / incomplete connections
    """
    last_error: Exception | None = None
    last_status: int | str | None = None

    with httpx.Client(timeout=PAGESPEED_TIMEOUT_SECONDS) as client:
        for attempt in range(1, PAGESPEED_MAX_ATTEMPTS + 1):
            try:
                response = client.get(
                    PAGESPEED_ENDPOINT,
                    params=[
                        ("url", url),
                        ("strategy", strategy),
                        ("key", settings.Pagespeed_API),
                        ("category", "performance"),
                        ("category", "seo"),
                        ("category", "accessibility"),
                        ("category", "best-practices"),
                    ],
                )
                last_status = response.status_code

                if response.status_code == 500:
                    if attempt < PAGESPEED_MAX_ATTEMPTS:
                        delay = PAGESPEED_RETRY_BASE_DELAY_SECONDS * attempt
                        logger.warning(
                            f"page_audit: PageSpeed 500 for {url} ({strategy}), "
                            f"attempt={attempt}/{PAGESPEED_MAX_ATTEMPTS}, "
                            f"retrying in {delay}s"
                        )
                        time.sleep(delay)
                        continue
                    return _empty_ps_result(
                        f"PageSpeed failed (status=500) after "
                        f"{PAGESPEED_MAX_ATTEMPTS} attempts"
                    )

                if response.status_code != 200:
                    return _empty_ps_result(
                        f"PageSpeed failed (status={response.status_code})"
                    )

                return _parse_pagespeed_payload(response.json())

            except _PAGESPEED_RETRY_EXCEPTIONS as exc:
                last_error = exc
                if attempt < PAGESPEED_MAX_ATTEMPTS:
                    delay = PAGESPEED_RETRY_BASE_DELAY_SECONDS * attempt
                    logger.warning(
                        f"page_audit: PageSpeed transient error for {url} "
                        f"({strategy}) — {exc}; "
                        f"attempt={attempt}/{PAGESPEED_MAX_ATTEMPTS}, "
                        f"retrying in {delay}s"
                    )
                    time.sleep(delay)
                    continue
                logger.error(
                    f"page_audit: PageSpeed failed after "
                    f"{PAGESPEED_MAX_ATTEMPTS} attempts for {url} ({strategy}) — {exc}"
                )
                return _empty_ps_result(
                    f"PageSpeed error after {PAGESPEED_MAX_ATTEMPTS} attempts: {exc}"
                )
            except Exception as exc:
                logger.exception(
                    f"page_audit: PageSpeed error for {url} ({strategy}) — {exc}"
                )
                return _empty_ps_result(f"PageSpeed error: {exc}")

    if last_error is not None:
        return _empty_ps_result(
            f"PageSpeed error after {PAGESPEED_MAX_ATTEMPTS} attempts: {last_error}"
        )
    return _empty_ps_result(
        f"PageSpeed failed (status={last_status or 'no_response'})"
    )


def _fetch_html(url: str) -> dict:
    result = {
        "status": None,
        "schema": False,
        "missing_meta": False,
        "missing_alt_count": 0,
        "internal_links": [],
        "error": None,
    }
    try:
        with httpx.Client(
            timeout=20.0,
            follow_redirects=True,
            headers=HTML_HEADERS,
        ) as client:
            response = client.get(url)
            result["status"] = response.status_code

            if response.status_code >= 400:
                result["error"] = f"HTTP {response.status_code}"
                return result

            soup = BeautifulSoup(response.text, "lxml")
            result["schema"] = bool(
                soup.find_all("script", attrs={"type": "application/ld+json"})
            )

            meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
            content = (meta.get("content") or "").strip() if meta else ""
            result["missing_meta"] = not bool(content)

            missing_alt = 0
            for img in soup.find_all("img"):
                alt = img.get("alt")
                if alt is None or not str(alt).strip():
                    missing_alt += 1
            result["missing_alt_count"] = missing_alt

            host = urlparse(url).netloc.lower()
            links: list[str] = []
            for tag in soup.find_all("a", href=True):
                href = tag["href"].strip()
                if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                absolute = urljoin(url, href)
                parsed = urlparse(absolute)
                if parsed.scheme in ("http", "https") and parsed.netloc.lower() == host:
                    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                    if clean not in links:
                        links.append(clean)
            result["internal_links"] = links
            return result
    except Exception as exc:
        logger.warning(f"page_audit: HTML fetch failed for {url} — {exc}")
        result["error"] = str(exc)
        return result


def _check_link_status(url: str) -> int | None:
    try:
        with httpx.Client(
            timeout=10.0,
            follow_redirects=True,
            headers=HTML_HEADERS,
        ) as client:
            response = client.head(url)
            if response.status_code in (405, 403):
                response = client.get(url)
            return response.status_code
    except Exception:
        return None


def _count_broken_links(links: list[str]) -> int:
    unique = list(dict.fromkeys(links))[:MAX_INTERNAL_LINKS_TO_CHECK]
    if not unique:
        return 0

    broken = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_check_link_status, link): link for link in unique}
        for future in as_completed(futures):
            status = future.result()
            if status is None or status >= 400:
                broken += 1
    return broken


def _fetch_robots_txt(base_url: str) -> str:
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True, headers=HTML_HEADERS) as client:
            response = client.get(f"{base_url}/robots.txt")
            if response.status_code == 200:
                return response.text
    except Exception as exc:
        logger.warning(f"page_audit: robots.txt fetch failed — {exc}")
    return ""


def _robots_blocks_path(robots_text: str, path: str) -> bool:
    if not robots_text:
        return False

    in_star = False
    for raw_line in robots_text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("user-agent:"):
            agent = line.split(":", 1)[1].strip()
            in_star = agent == "*"
            continue
        if not in_star:
            continue
        if lower.startswith("disallow:"):
            rule = line.split(":", 1)[1].strip()
            if not rule:
                continue
            if path.startswith(rule) or path.rstrip("/") == rule.rstrip("/"):
                return True
    return False


def _sitemap_listed_in_robots(robots_text: str) -> bool:
    for raw_line in robots_text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line.lower().startswith("sitemap:"):
            return True
    return False


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _build_strategy_block(
    ps: dict,
    html: dict,
    path: str,
    blocked_by_robots: bool,
) -> dict:
    """Build mobile/desktop block with scores, LCP, issues, total_issues."""
    issues: list[str] = list(ps.get("issues") or [])

    if html.get("error") and html.get("status") is None:
        issues.append(f"Page fetch failed: {html['error']}")
    elif html.get("status") and html["status"] >= 400:
        issues.append(f"HTTP status {html['status']}")

    if not html.get("schema"):
        issues.append("JSON-LD schema missing")

    # Prefer HTML meta/alt when PageSpeed did not already flag them
    if html.get("missing_meta") and "Missing meta description" not in issues:
        issues.append("Missing meta description")

    html_alt = int(html.get("missing_alt_count") or 0)
    if html_alt and not any("images missing alt text" in i for i in issues):
        issues.append(f"{html_alt} images missing alt text")

    if blocked_by_robots:
        issues.append(f"robots.txt blocks {path}")

    issues = _dedupe(issues)

    return {
        "performance": ps.get("performance"),
        "seo": ps.get("seo"),
        "accessibility": ps.get("accessibility"),
        "best_practices": ps.get("best_practices"),
        "lcp": ps.get("lcp"),
        "total_issues": len(issues),
        "issues": issues,
    }


def _build_site_issues(
    pages: list[dict],
    broken_links: int,
    robots_blocked_paths: list[str],
    sitemap_in_robots: bool,
) -> list[str]:
    site_issues: list[str] = []

    # Slow LCP: count a page once if mobile or desktop is slow; avg of that page's worst LCP
    slow_lcps: list[float] = []
    for page in pages:
        candidates = []
        for key in ("mobile", "desktop"):
            lcp = page.get(key, {}).get("lcp")
            if lcp is not None and lcp > LCP_SLOW_SECONDS:
                candidates.append(lcp)
        if candidates:
            slow_lcps.append(max(candidates))

    if slow_lcps:
        avg = sum(slow_lcps) / len(slow_lcps)
        site_issues.append(
            f"Slow LCP on {len(slow_lcps)} pages (avg {avg:.1f}s)"
        )

    total_alt = sum(int(p.get("missing_alt_count") or 0) for p in pages)
    if total_alt:
        site_issues.append(f"Missing alt text ({total_alt} images)")

    missing_meta_pages = sum(1 for p in pages if p.get("missing_meta"))
    if missing_meta_pages:
        site_issues.append(f"{missing_meta_pages} pages missing meta descriptions")

    if broken_links:
        site_issues.append(f"{broken_links} broken internal links")

    for path in robots_blocked_paths:
        site_issues.append(f"robots.txt blocks {path} page")

    if not sitemap_in_robots:
        site_issues.append("Sitemap.xml not listed in robots.txt")

    return site_issues


async def run_page_audit(urls: list[str]) -> dict:
    page_entries: list[tuple[str, str, str]] = []
    seen = set()
    for raw in urls:
        full, path, base = _normalize_page_url(raw)
        if full in seen:
            continue
        seen.add(full)
        page_entries.append((full, path, base))

    if not page_entries:
        raise ValueError("At least one valid URL is required")

    logger.info(
        f"page_audit: starting — pages={len(page_entries)}, "
        f"pagespeed_jobs={len(page_entries) * len(STRATEGIES)}, workers={MAX_WORKERS}"
    )

    # 1) HTML fetch
    html_by_url: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_html, full): full for full, _, _ in page_entries
        }
        for future in as_completed(futures):
            full = futures[future]
            html_by_url[full] = future.result()

    # 2) PageSpeed mobile + desktop (max 5 at once)
    semaphore = asyncio.Semaphore(MAX_WORKERS)

    async def _run_ps(url: str, strategy: str) -> dict:
        async with semaphore:
            return await asyncio.to_thread(_pagespeed_request, url, strategy)

    ps_jobs = []
    for full, _, _ in page_entries:
        for strategy in STRATEGIES:
            ps_jobs.append((full, strategy, _run_ps(full, strategy)))

    ps_results = await asyncio.gather(*(job for _, _, job in ps_jobs))

    ps_by_url: dict[str, dict[str, dict]] = {full: {} for full, _, _ in page_entries}
    for (full, strategy, _), result in zip(ps_jobs, ps_results):
        ps_by_url[full][strategy] = result

    # 3) robots.txt
    bases = list(dict.fromkeys(base for _, _, base in page_entries))
    robots_by_base: dict[str, str] = {}
    for base in bases:
        robots_by_base[base] = await asyncio.to_thread(_fetch_robots_txt, base)

    primary_robots = robots_by_base.get(page_entries[0][2], "")
    sitemap_in_robots = _sitemap_listed_in_robots(primary_robots)

    robots_blocked_paths: list[str] = []
    blocked_set: set[str] = set()
    for full, path, base in page_entries:
        if _robots_blocks_path(robots_by_base.get(base, ""), path):
            robots_blocked_paths.append(path)
            blocked_set.add(full)

    # 4) Broken internal links
    all_links: list[str] = []
    for full, _, _ in page_entries:
        all_links.extend(html_by_url.get(full, {}).get("internal_links") or [])
    broken_links = await asyncio.to_thread(_count_broken_links, all_links)

    # 5) Per-page response
    pages_out: list[dict] = []
    for full, path, _base in page_entries:
        html = html_by_url.get(full) or {}
        mobile_ps = ps_by_url.get(full, {}).get("mobile") or {}
        desktop_ps = ps_by_url.get(full, {}).get("desktop") or {}
        blocked = full in blocked_set

        missing_meta = bool(
            html.get("missing_meta")
            or mobile_ps.get("missing_meta")
            or desktop_ps.get("missing_meta")
        )
        missing_alt = int(html.get("missing_alt_count") or 0)
        if not missing_alt:
            missing_alt = max(
                int(mobile_ps.get("missing_alt_count") or 0),
                int(desktop_ps.get("missing_alt_count") or 0),
            )

        pages_out.append(
            {
                "path": path,
                "url": full,
                "status": html.get("status"),
                "mobile": _build_strategy_block(mobile_ps, html, path, blocked),
                "desktop": _build_strategy_block(desktop_ps, html, path, blocked),
                "missing_meta": missing_meta,
                "missing_alt_count": missing_alt,
            }
        )

    site_issues = _build_site_issues(
        pages_out,
        broken_links=broken_links,
        robots_blocked_paths=list(dict.fromkeys(robots_blocked_paths)),
        sitemap_in_robots=sitemap_in_robots,
    )

    public_urls = [
        {
            "path": page["path"],
            "url": page["url"],
            "status": page["status"],
            "mobile": page["mobile"],
            "desktop": page["desktop"],
        }
        for page in pages_out
    ]

    logger.info(
        f"page_audit: done — pages={len(public_urls)}, "
        f"site_issues={len(site_issues)}, broken_links={broken_links}"
    )

    return {
        "status": "success",
        "urls": public_urls,
        "issues": site_issues,
    }
