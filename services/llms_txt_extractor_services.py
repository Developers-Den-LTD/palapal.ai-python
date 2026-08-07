"""
STEP 3 of the llms.txt pipeline: open each selected page and read useful content.

For every URL we extract title, meta description, headings, paragraphs,
image alt text, and any phone/email found on the page.
Pages are fetched in parallel (async) to save time.
"""

import asyncio
import re

import httpx
from bs4 import BeautifulSoup

from services.logger_services import logger

# Fetch up to 8 pages at the same time.
MAX_CONCURRENT_REQUESTS = 8
REQUEST_TIMEOUT = 15.0
# Limits so we do not send huge text blobs to the LLM.
MAX_HEADINGS = 12
MAX_PARAGRAPHS = 5
MAX_PARAGRAPH_CHARS = 500
MAX_IMAGE_ALTS = 10

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {"User-Agent": USER_AGENT}

# Patterns to find phone numbers and emails in page text.
PHONE_PATTERN = re.compile(
    r"(?:\+?\d{1,3}[\s\-]?)?(?:\(?\d{2,4}\)?[\s\-]?)?\d{3,4}[\s\-]?\d{3,4}(?:[\s\-]?\d{3,4})?"
)
EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)


def _log_section(title: str) -> None:
    logger.info(f"llms_txt_extractor: {'=' * 60}")
    logger.info(f"llms_txt_extractor: {title}")
    logger.info(f"llms_txt_extractor: {'=' * 60}")


def _clean_text(text: str) -> str:
    """Collapse extra spaces and trim whitespace."""
    return re.sub(r"\s+", " ", text or "").strip()


def _extract_visible_text_blocks(soup: BeautifulSoup) -> BeautifulSoup:
    """Remove scripts, nav, footer, etc. so we only read main page content."""
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()

    for selector in ("nav", "footer", "header"):
        for tag in soup.find_all(selector):
            tag.decompose()

    return soup


def _extract_meta_description(soup: BeautifulSoup) -> str:
    """Get the page summary from <meta name=\"description\"> or og:description."""
    meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if meta and meta.get("content"):
        return _clean_text(meta["content"])

    og_meta = soup.find("meta", attrs={"property": re.compile(r"^og:description$", re.I)})
    if og_meta and og_meta.get("content"):
        return _clean_text(og_meta["content"])

    return ""


def _extract_headings(soup: BeautifulSoup) -> list[dict]:
    """Collect h1 and h2 headings (main section titles on the page)."""
    headings: list[dict] = []
    for level in (1, 2):
        for tag in soup.find_all(f"h{level}"):
            text = _clean_text(tag.get_text(" ", strip=True))
            if text and len(text) <= 200:
                headings.append({"level": level, "text": text})
            if len(headings) >= MAX_HEADINGS:
                return headings
    return headings


def _extract_paragraphs(soup: BeautifulSoup) -> list[str]:
    """Collect the first few meaningful <p> blocks of text."""
    paragraphs: list[str] = []
    for tag in soup.find_all("p"):
        text = _clean_text(tag.get_text(" ", strip=True))
        if len(text) < 30:
            continue
        if len(text) > MAX_PARAGRAPH_CHARS:
            text = text[:MAX_PARAGRAPH_CHARS].rstrip() + "..."
        paragraphs.append(text)
        if len(paragraphs) >= MAX_PARAGRAPHS:
            break
    return paragraphs


def _extract_image_alts(soup: BeautifulSoup) -> list[str]:
    """Collect image alt text (describes pictures on the page)."""
    alts: list[str] = []
    for img in soup.find_all("img"):
        alt = _clean_text(img.get("alt", ""))
        if alt and len(alt) >= 3:
            alts.append(alt)
        if len(alts) >= MAX_IMAGE_ALTS:
            break
    return alts


def _extract_contact_hints(text: str) -> dict:
    """Find phone numbers and emails anywhere in the visible page text."""
    phones = []
    for match in PHONE_PATTERN.findall(text):
        cleaned = _clean_text(match)
        if len(re.sub(r"\D", "", cleaned)) >= 8:
            phones.append(cleaned)

    emails = EMAIL_PATTERN.findall(text)

    return {
        "phones": list(dict.fromkeys(phones))[:5],
        "emails": list(dict.fromkeys(emails))[:5],
    }


def parse_html_page(url: str, html: str) -> dict:
    """
    Turn raw HTML into a structured dict the LLM can use.
    This is the main extraction function for a single page.
    """
    soup = BeautifulSoup(html, "html.parser")
    soup = _extract_visible_text_blocks(soup)

    title = _clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    meta_description = _extract_meta_description(soup)
    headings = _extract_headings(soup)
    paragraphs = _extract_paragraphs(soup)
    image_alts = _extract_image_alts(soup)

    visible_text = _clean_text(soup.get_text(" ", strip=True))
    contact = _extract_contact_hints(visible_text)
    content = "\n\n".join(paragraphs)

    return {
        "url": url,
        "title": title,
        "headings": headings,
        "description": meta_description,
        "content": content,
        "business_info": {
            "phones": contact["phones"],
            "emails": contact["emails"],
        },
        # Kept for backward compatibility with existing pipeline code.
        "meta_description": meta_description,
        "paragraphs": paragraphs,
        "image_alts": image_alts,
        "phones": contact["phones"],
        "emails": contact["emails"],
        "status": "success",
        "error": None,
    }


def _empty_page_result(url: str, error: str) -> dict:
    """Return a failed result when a page could not be loaded or parsed."""
    return {
        "url": url,
        "title": "",
        "headings": [],
        "description": "",
        "content": "",
        "business_info": {"phones": [], "emails": []},
        "meta_description": "",
        "paragraphs": [],
        "image_alts": [],
        "phones": [],
        "emails": [],
        "status": "error",
        "error": error,
    }


async def _fetch_and_extract(
    client: httpx.AsyncClient,
    url: str,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Download one URL and extract its content (runs inside concurrency limit)."""
    async with semaphore:
        try:
            response = await client.get(url)
            if response.status_code != 200:
                error = f"HTTP {response.status_code}"
                logger.warning(f"llms_txt_extractor: failed '{url}' — {error}")
                return _empty_page_result(url, error)

            result = parse_html_page(url, response.text)
            logger.info(
                f"llms_txt_extractor: extracted '{url}' — "
                f"title='{result['title'][:80]}', "
                f"headings={len(result['headings'])}, "
                f"paragraphs={len(result['paragraphs'])}"
            )
            return result
        except httpx.TimeoutException:
            logger.warning(f"llms_txt_extractor: timeout for '{url}'")
            return _empty_page_result(url, "Request timed out")
        except httpx.RequestError as exc:
            logger.warning(f"llms_txt_extractor: request failed for '{url}' — {exc}")
            return _empty_page_result(url, str(exc))
        except Exception as exc:
            logger.exception(f"llms_txt_extractor: unexpected error for '{url}' — {exc}")
            return _empty_page_result(url, str(exc))


async def extract_pages_async(
    urls: list[str],
    max_workers: int = MAX_CONCURRENT_REQUESTS,
) -> list[dict]:
    """
    Open all selected URLs in parallel and extract content from each.
    Returns a list of page data dicts (one per URL).
    """
    _log_section("Step 3 — Async page extraction")
    logger.info(
        f"llms_txt_extractor: extracting {len(urls)} page(s) "
        f"with {max_workers} worker(s)"
    )

    if not urls:
        return []

    semaphore = asyncio.Semaphore(max_workers)
    timeout = httpx.Timeout(REQUEST_TIMEOUT)

    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        follow_redirects=True,
        timeout=timeout,
    ) as client:
        tasks = [
            _fetch_and_extract(client, url, semaphore)
            for url in urls
        ]
        results = await asyncio.gather(*tasks)

    successful = sum(1 for item in results if item["status"] == "success")
    logger.info(
        f"llms_txt_extractor: completed — "
        f"successful={successful}/{len(results)}"
    )
    return list(results)
