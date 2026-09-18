"""
AI Keyword Generation.

1. Crawl the business website and extract titles, headings, paragraphs,
   image alt text, and meta descriptions.
2. Merge optional client-supplied existing_content.
3. Ask AI providers (with model fallbacks) to generate categorized keywords.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from schema.ai_keyword_generation_schema import AIKeywordGenerationRequest
from services.ai_provider_services import (
    PROVIDER_FETCHERS,
    PROVIDER_MODEL_FALLBACKS,
)
from services.llms_txt_crawler_services import (
    discover_website_urls,
    filter_and_rank_urls,
    normalize_website_url,
)
from services.llms_txt_extractor_services import extract_pages_async
from services.logger_services import logger

KEYWORD_CATEGORIES = (
    "primary",
    "secondary",
    "long_tail",
    "local_seo",
    "service_based",
    "question_based",
)

# Keep extraction light so keyword jobs finish in a reasonable time.
MAX_PAGES_FOR_KEYWORDS = 8
MAX_KEYWORDS_PER_CATEGORY = 10
PROVIDER_ORDER = ("openai", "gemini", "anthropic", "perplexity", "llama")


def _log_section(title: str) -> None:
    logger.info(f"ai_keyword_generation: {'=' * 60}")
    logger.info(f"ai_keyword_generation: {title}")
    logger.info(f"ai_keyword_generation: {'=' * 60}")


def _extract_json(raw_text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw_text or "").strip())
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("The model did not return a JSON object")
    return json.loads(cleaned[start : end + 1])


def _empty_suggestions() -> dict[str, list[str]]:
    return {category: [] for category in KEYWORD_CATEGORIES}


def _normalize_keyword(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip().lower())
    return cleaned.strip(" .,;:!?\"'")


def _clean_keyword_list(values: Any, *, limit: int = MAX_KEYWORDS_PER_CATEGORY) -> list[str]:
    if not isinstance(values, list):
        return []

    cleaned: list[str] = []
    seen: set[str] = set()

    for item in values:
        text = re.sub(r"\s+", " ", str(item or "").strip())
        if not text:
            continue
        key = _normalize_keyword(text)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= limit:
            break

    return cleaned


def _normalize_suggestions(raw: dict) -> dict[str, list[str]]:
    suggestions = _empty_suggestions()

    # Accept either flat keys or a nested "keywords" / "suggestions" object.
    source = raw
    for nest_key in ("keywords", "suggestions"):
        nested = raw.get(nest_key)
        if isinstance(nested, dict):
            source = nested
            break

    aliases = {
        "primary": ("primary", "primary_keywords"),
        "secondary": ("secondary", "secondary_keywords"),
        "long_tail": ("long_tail", "long-tail", "long_tail_keywords", "longtail"),
        "local_seo": ("local_seo", "local-seo", "local_seo_keywords", "local"),
        "service_based": (
            "service_based",
            "service-based",
            "service_based_keywords",
            "services",
        ),
        "question_based": (
            "question_based",
            "question-based",
            "question_based_keywords",
            "questions",
        ),
    }

    for category, keys in aliases.items():
        for key in keys:
            if key in source:
                suggestions[category] = _clean_keyword_list(source.get(key))
                break

    return suggestions


def _suggestions_are_usable(suggestions: dict[str, list[str]]) -> bool:
    return any(suggestions.get(category) for category in KEYWORD_CATEGORIES)


def _summarize_pages(pages: list[dict]) -> list[dict]:
    summarized: list[dict] = []
    for page in pages:
        if page.get("status") != "success":
            continue
        headings = [
            item.get("text", "")
            for item in (page.get("headings") or [])
            if isinstance(item, dict) and item.get("text")
        ]
        summarized.append(
            {
                "url": page.get("url", ""),
                "title": page.get("title", ""),
                "meta_description": page.get("meta_description")
                or page.get("description")
                or "",
                "headings": headings[:12],
                "paragraphs": (page.get("paragraphs") or [])[:5],
                "image_alts": (page.get("image_alts") or [])[:10],
            }
        )
    return summarized


def _fetch_website_content(website_url: str) -> dict:
    _log_section("Step 1 — Website content extraction")
    base_url = normalize_website_url(website_url)
    logger.info(f"ai_keyword_generation: crawling website_url='{base_url}'")

    try:
        discovered_base, discovered_urls = discover_website_urls(base_url)
        selected_urls = filter_and_rank_urls(discovered_base, discovered_urls)[
            :MAX_PAGES_FOR_KEYWORDS
        ]

        if not selected_urls:
            selected_urls = [discovered_base]

        pages = asyncio.run(extract_pages_async(selected_urls))
        summarized = _summarize_pages(pages)
        successful = len(summarized)

        logger.info(
            "ai_keyword_generation: website extraction done — "
            f"selected={len(selected_urls)}, successful_pages={successful}"
        )

        return {
            "status": "success" if successful else "partial",
            "website_url": discovered_base,
            "pages_selected": len(selected_urls),
            "pages_extracted": successful,
            "pages": summarized,
            "error": None if successful else "No page content could be extracted",
        }
    except Exception as exc:
        logger.exception(
            f"ai_keyword_generation: website extraction failed — {exc}"
        )
        return {
            "status": "error",
            "website_url": website_url,
            "pages_selected": 0,
            "pages_extracted": 0,
            "pages": [],
            "error": str(exc),
        }


def _build_prompt(payload: AIKeywordGenerationRequest, website_content: dict) -> str:
    services_text = ", ".join(payload.services)
    pages = website_content.get("pages") or []
    pages_blob = json.dumps(pages, ensure_ascii=False, indent=2)
    existing = payload.existing_content or ""

    return f"""You are an SEO keyword strategist for local businesses.

Generate relevant search keywords for this business using ONLY the information below.
Return ONLY valid JSON. No markdown. No explanations.

Business name: {payload.business_name}
Industry: {payload.industry}
Services: {services_text}
Location: {payload.location}
Website URL: {payload.website_url}

Website content (titles, meta descriptions, headings, paragraphs, image alt text):
{pages_blob}

Optional existing content from the client:
{existing if existing else "(none)"}

Rules:
- Create realistic search phrases a customer would type.
- Include the location in local SEO and many primary/secondary terms when natural.
- Use services and website wording for service-based and long-tail terms.
- Question-based keywords should look like natural questions people ask Google or AI assistants.
- Do not invent services the business clearly does not offer.
- Provide up to {MAX_KEYWORDS_PER_CATEGORY} keywords per category.
- Deduplicate keywords within and across categories when possible.
- Prefer lowercase phrases without trailing punctuation.

Return JSON in this exact shape:
{{
  "primary": ["..."],
  "secondary": ["..."],
  "long_tail": ["..."],
  "local_seo": ["..."],
  "service_based": ["..."],
  "question_based": ["..."]
}}
"""


def _generate_with_providers(prompt: str) -> dict:
    _log_section("Step 2 — AI keyword generation")
    last_error: Exception | None = None
    attempts: list[dict] = []

    for provider in PROVIDER_ORDER:
        models = PROVIDER_MODEL_FALLBACKS.get(provider) or []
        fetcher = PROVIDER_FETCHERS.get(provider)
        if not fetcher or not models:
            continue

        for model in models:
            logger.info(
                "ai_keyword_generation: calling "
                f"provider='{provider}' model='{model}'"
            )
            try:
                raw_text = fetcher(prompt, model)
                parsed = _extract_json(raw_text)
                suggestions = _normalize_suggestions(parsed)

                if not _suggestions_are_usable(suggestions):
                    raise ValueError("Model returned empty keyword categories")

                total = sum(len(suggestions[c]) for c in KEYWORD_CATEGORIES)
                logger.info(
                    "ai_keyword_generation: success — "
                    f"provider='{provider}' model='{model}' "
                    f"total_keywords={total}"
                )
                return {
                    "status": "success",
                    "provider": provider,
                    "model": model,
                    "suggestions": suggestions,
                    "attempts": attempts
                    + [
                        {
                            "provider": provider,
                            "model": model,
                            "status": "success",
                        }
                    ],
                }
            except Exception as exc:
                last_error = exc
                attempts.append(
                    {
                        "provider": provider,
                        "model": model,
                        "status": "error",
                        "error": str(exc),
                    }
                )
                logger.warning(
                    "ai_keyword_generation: provider failed — "
                    f"provider='{provider}' model='{model}' error={exc}"
                )

    raise RuntimeError(
        f"All AI providers failed to generate keywords. Last error: {last_error}"
    )


def run_ai_keyword_generation(payload: AIKeywordGenerationRequest) -> dict:
    _log_section("AI Keyword Generation — started")
    logger.info(
        "ai_keyword_generation: "
        f"business='{payload.business_name}', "
        f"business_id='{payload.business_id}', "
        f"industry='{payload.industry}', "
        f"location='{payload.location}', "
        f"services={len(payload.services)}, "
        f"website='{payload.website_url}', "
        f"has_existing_content={bool(payload.existing_content)}"
    )

    website_content = _fetch_website_content(payload.website_url)
    prompt = _build_prompt(payload, website_content)
    generation = _generate_with_providers(prompt)
    suggestions = generation["suggestions"]

    total_keywords = sum(len(suggestions[c]) for c in KEYWORD_CATEGORIES)

    result = {
        "status": "success",
        "business_name": payload.business_name,
        "business_id": payload.business_id,
        "industry": payload.industry,
        "services": payload.services,
        "location": payload.location,
        "website_url": website_content.get("website_url") or payload.website_url,
        "provider": generation["provider"],
        "model": generation["model"],
        "total_keywords": total_keywords,
        "suggestions": suggestions,
        "website_content": {
            "status": website_content.get("status"),
            "pages_selected": website_content.get("pages_selected", 0),
            "pages_extracted": website_content.get("pages_extracted", 0),
            "error": website_content.get("error"),
            "pages": website_content.get("pages") or [],
        },
        "provider_attempts": generation.get("attempts") or [],
    }

    logger.info(
        "ai_keyword_generation: completed — "
        f"status=success, total_keywords={total_keywords}, "
        f"provider='{generation['provider']}', model='{generation['model']}'"
    )
    return result
