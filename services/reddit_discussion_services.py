"""
Reddit discussion analysis for a specific business.

1. Search Reddit via DuckDuckGo (site:reddit.com) for real evidence.
2. Summarize only that evidence with OpenAI (model fallbacks).
3. If nothing relevant is found, return a clear not-found response.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urlparse

from ddgs import DDGS
from openai import OpenAI

from core.config import settings
from schema.reddit_discussion_schema import RedditDiscussionRequest
from services.logger_services import logger

OPENAI_MODEL_FALLBACKS = [
    "gpt-4o-mini",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
]

SCRAPED_AT_FORMAT = "%d %B %Y %H:%M"
MAX_SEARCH_RESULTS = 25
MAX_EVIDENCE_ITEMS = 15
NOT_FOUND_MESSAGE = (
    "I did not find any Reddit discussion about that specific business."
)


def _log_section(title: str) -> None:
    logger.info(f"reddit_discussion: {'=' * 60}")
    logger.info(f"reddit_discussion: {title}")
    logger.info(f"reddit_discussion: {'=' * 60}")


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _is_reddit_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return host == "reddit.com" or host.endswith(".reddit.com")


def _normalize_reddit_url(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/")
    return f"https://www.reddit.com{path}"


def _business_mentioned(business_name: str, *parts: str) -> bool:
    """Require the business name (or most significant tokens) to appear in text."""
    haystack = _normalize_text(" ".join(p for p in parts if p))
    if not haystack:
        return False

    needle = _normalize_text(business_name)
    if not needle:
        return False

    if needle in haystack:
        return True

    tokens = [t for t in needle.split() if len(t) > 2]
    if len(tokens) >= 2:
        # Allow partial match when most distinctive tokens appear.
        matched = sum(1 for token in tokens if token in haystack)
        return matched >= max(2, len(tokens) - 1)

    return False


def _search_reddit_discussions(business_name: str, business_loc: str) -> list[dict]:
    """Collect Reddit search hits for this business only."""
    queries = [
        f'site:reddit.com "{business_name}" "{business_loc}"',
        f'site:reddit.com "{business_name}" {business_loc}',
        f'site:reddit.com "{business_name}"',
    ]

    seen_urls: set[str] = set()
    evidence: list[dict] = []

    with DDGS() as ddgs:
        for query in queries:
            logger.info(f"reddit_discussion: search query='{query}'")
            try:
                results = ddgs.text(query, max_results=MAX_SEARCH_RESULTS) or []
            except Exception as exc:
                logger.warning(
                    f"reddit_discussion: search failed for query='{query}' — {exc}"
                )
                continue

            for item in results:
                url = str(item.get("href") or item.get("link") or "").strip()
                title = str(item.get("title") or "").strip()
                body = str(item.get("body") or item.get("snippet") or "").strip()

                if not url or not _is_reddit_url(url):
                    continue

                normalized_url = _normalize_reddit_url(url)
                if normalized_url in seen_urls:
                    continue

                if not _business_mentioned(business_name, title, body, normalized_url):
                    continue

                seen_urls.add(normalized_url)
                evidence.append(
                    {
                        "title": title,
                        "snippet": body,
                        "url": normalized_url,
                    }
                )

                if len(evidence) >= MAX_EVIDENCE_ITEMS:
                    return evidence

    return evidence


def _extract_json(raw_text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("The model did not return a JSON object")
    return json.loads(cleaned[start : end + 1])


def _build_analysis_prompt(
    business_name: str,
    business_loc: str,
    evidence: list[dict],
) -> str:
    return f"""You analyze Reddit discussions about ONE specific business.

Business name: {business_name}
Location: {business_loc}

Evidence from Reddit search results (JSON):
{json.dumps(evidence, ensure_ascii=False, indent=2)}

Rules:
1. Use ONLY the evidence above. Do not invent posts, comments, subreddits, or URLs.
2. Discuss ONLY this exact business. Ignore unrelated businesses with similar names.
3. If the evidence is empty, weak, or not clearly about this business in this location context, set found=false.
4. Prefer location-relevant discussion when available, but still allow clearly matching brand discussion.
5. Return ONLY valid JSON in this exact shape:
{{
  "found": true,
  "reddit_sentiment": "Positive" | "Negative" | "Mixed" | "Neutral",
  "reddit_visibility_score": 0,
  "summary": "2-4 sentence overview of what Reddit is saying",
  "key_insights": ["insight"],
  "positive_comments": ["positive point from Reddit"],
  "negative_comments": ["negative point from Reddit"],
  "common_discussions": ["recurring topic people talk about"],
  "relevant_subreddits": ["r/example"],
  "sources": ["https://www.reddit.com/..."]
}}

When found=false, return:
{{
  "found": false,
  "reddit_sentiment": "None",
  "reddit_visibility_score": 0,
  "summary": "{NOT_FOUND_MESSAGE}",
  "key_insights": [],
  "positive_comments": [],
  "negative_comments": [],
  "common_discussions": [],
  "relevant_subreddits": [],
  "sources": []
}}

Visibility score guidance when found=true:
- 1-30: sparse mentions
- 31-70: moderate discussion
- 71-100: frequent / highly visible discussion
"""


def _as_string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _as_reddit_sources(value) -> list[str]:
    sources: list[str] = []
    seen: set[str] = set()
    for item in _as_string_list(value):
        if not _is_reddit_url(item):
            continue
        normalized = _normalize_reddit_url(item)
        if normalized in seen:
            continue
        seen.add(normalized)
        sources.append(normalized)
    return sources


def _as_subreddits(value) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in _as_string_list(value):
        name = item.strip()
        if not name:
            continue
        if not name.startswith("r/"):
            name = f"r/{name.lstrip('/')}"
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(name)
    return cleaned


def _call_openai_with_fallback(prompt: str) -> tuple[dict, str]:
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    last_error: Exception | None = None

    for model in OPENAI_MODEL_FALLBACKS:
        try:
            logger.info(f"reddit_discussion: calling OpenAI model='{model}'")
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a careful Reddit research analyst. "
                            "Never invent Reddit content. Return only valid JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            raw = (response.choices[0].message.content or "").strip()
            if not raw:
                raise ValueError("OpenAI returned empty content")

            parsed = _extract_json(raw)
            if not isinstance(parsed, dict):
                raise ValueError("OpenAI JSON root must be an object")

            logger.info(
                f"reddit_discussion: OpenAI model='{model}' analysis succeeded"
            )
            return parsed, model
        except Exception as exc:
            last_error = exc
            logger.warning(
                f"reddit_discussion: OpenAI model='{model}' failed — {exc}"
            )

    raise RuntimeError(
        f"Reddit discussion analysis failed after OpenAI model fallbacks — {last_error}"
    )


def _not_found_response(
    business_name: str,
    business_loc: str,
    *,
    model: str | None = None,
) -> dict:
    return {
        "status": "success",
        "found": False,
        "message": NOT_FOUND_MESSAGE,
        "business_name": business_name,
        "business_loc": business_loc,
        "scraped_at": datetime.now().strftime(SCRAPED_AT_FORMAT),
        "reddit_data_found": False,
        "reddit_sentiment": "None",
        "reddit_visibility_score": 0,
        "summary": NOT_FOUND_MESSAGE,
        "key_insights": [],
        "positive_comments": [],
        "negative_comments": [],
        "common_discussions": [],
        "common_praises": [],
        "common_complaints": [],
        "relevant_subreddits": [],
        "sources": [],
        "model_used": model,
    }


def _normalize_analysis(
    analysis: dict,
    *,
    business_name: str,
    business_loc: str,
    evidence: list[dict],
    model: str,
) -> dict:
    found = bool(analysis.get("found"))
    sources = _as_reddit_sources(analysis.get("sources"))
    if not sources:
        sources = [item["url"] for item in evidence]

    positive = _as_string_list(analysis.get("positive_comments"))
    negative = _as_string_list(analysis.get("negative_comments"))
    common = _as_string_list(analysis.get("common_discussions"))
    insights = _as_string_list(analysis.get("key_insights"))
    subreddits = _as_subreddits(analysis.get("relevant_subreddits"))

    # Guardrails: if model claims found but returns no useful content, treat as not found.
    has_signal = bool(positive or negative or common or insights or sources)
    if not found or not has_signal:
        return _not_found_response(business_name, business_loc, model=model)

    try:
        visibility = int(float(analysis.get("reddit_visibility_score") or 0))
    except (TypeError, ValueError):
        visibility = 0
    visibility = max(0, min(100, visibility))

    sentiment = str(analysis.get("reddit_sentiment") or "Mixed").strip().title()
    if sentiment not in {"Positive", "Negative", "Mixed", "Neutral"}:
        sentiment = "Mixed"

    summary = str(analysis.get("summary") or "").strip()
    if not summary:
        summary = (
            f"Reddit discussion was found for {business_name} "
            f"related to {business_loc}."
        )

    return {
        "status": "success",
        "found": True,
        "message": "Reddit discussion found for this business.",
        "business_name": business_name,
        "business_loc": business_loc,
        "scraped_at": datetime.now().strftime(SCRAPED_AT_FORMAT),
        "reddit_data_found": True,
        "reddit_sentiment": sentiment,
        "reddit_visibility_score": visibility,
        "summary": summary,
        "key_insights": insights,
        "positive_comments": positive,
        "negative_comments": negative,
        "common_discussions": common,
        # Compatibility aliases with earlier Reddit sample payloads.
        "common_praises": positive,
        "common_complaints": negative,
        "relevant_subreddits": subreddits,
        "sources": sources,
        "model_used": model,
    }


def analyze_reddit_discussion(payload: RedditDiscussionRequest) -> dict:
    business_name = payload.business_name.strip()
    business_loc = payload.business_loc.strip()

    _log_section("Reddit discussion — start")
    logger.info(
        f"reddit_discussion: business='{business_name}', loc='{business_loc}'"
    )

    evidence = _search_reddit_discussions(business_name, business_loc)
    logger.info(
        f"reddit_discussion: collected {len(evidence)} relevant Reddit result(s)"
    )

    if not evidence:
        _log_section("Reddit discussion — not found")
        return _not_found_response(business_name, business_loc)

    prompt = _build_analysis_prompt(business_name, business_loc, evidence)
    analysis, model = _call_openai_with_fallback(prompt)
    result = _normalize_analysis(
        analysis,
        business_name=business_name,
        business_loc=business_loc,
        evidence=evidence,
        model=model,
    )

    _log_section("Reddit discussion — complete")
    logger.info(
        f"reddit_discussion: found={result['found']}, "
        f"sentiment={result['reddit_sentiment']}, "
        f"sources={len(result['sources'])}, model='{result.get('model_used')}'"
    )
    return result
