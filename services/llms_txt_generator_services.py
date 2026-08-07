"""
STEP 4 & 5 of the llms.txt pipeline: AI writes the file and we save it.

Orchestration:
  1. Crawl website recursively to discover URLs (same as /scraper/crawl-website).
  2. Filter and rank URLs, then extract page content.
  3. Send extracted data to OpenAI to write llms.txt.
  4. Save llms.txt and generation_metadata.json on disk.
"""

import json
import os
from datetime import datetime

from openai import OpenAI

from core.config import settings
from schema.llms_txt_generator_schema import (
    LlmsTxtFromUrlsRequest,
    LlmsTxtGeneratorRequest,
)
from services.llms_txt_crawler_services import normalize_website_url
from services.llms_txt_crawler_services import (
    discover_website_urls,
    filter_and_rank_urls,
)
from services.llms_txt_extractor_services import extract_pages_async
from services.logger_services import logger
from utils.scraped_result_paths import (
    get_llms_txt_metadata_path,
    get_llms_txt_output_folder,
    get_llms_txt_output_path,
)

# Primary model for llms.txt generation.
LLM_MODEL = "gpt-4o-mini"
LLM_MODEL_FALLBACKS = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"]


def _log_section(title: str) -> None:
    logger.info(f"llms_txt_generator: {'=' * 60}")
    logger.info(f"llms_txt_generator: {title}")
    logger.info(f"llms_txt_generator: {'=' * 60}")


def _infer_business_name(
    website_url: str,
    business_name: str | None,
    pages: list[dict],
) -> str:
    """
    Pick the site/business name for the llms.txt H1 heading.
    Uses the request value first, then page h1/title, then the domain name.
    """
    if business_name and business_name.strip():
        return business_name.strip()

    for page in pages:
        if page.get("status") != "success":
            continue
        for heading in page.get("headings", []):
            if heading.get("level") == 1 and heading.get("text"):
                return heading["text"]
        if page.get("title"):
            title = page["title"].split("|")[0].strip()
            if title:
                return title

    from urllib.parse import urlparse

    host = urlparse(website_url).netloc
    if host.startswith("www."):
        host = host[4:]
    return host.split(".")[0].replace("-", " ").title()


def _build_generation_prompt(
    business_name: str,
    base_url: str,
    pages: list[dict],
) -> str:
    """
    Build the prompt for OpenAI: all extracted page data + llms.txt format rules.
    The model must only use facts from this data, not invent content.
    """
    successful_pages = [page for page in pages if page.get("status") == "success"]
    payload = {
        "business_name": business_name,
        "website_url": base_url,
        "pages": [
            {
                "url": page["url"],
                "title": page.get("title"),
                "headings": page.get("headings"),
                "description": page.get("description") or page.get("meta_description"),
                "content": page.get("content") or "\n\n".join(page.get("paragraphs", [])),
                "business_info": page.get("business_info")
                or {
                    "phones": page.get("phones", []),
                    "emails": page.get("emails", []),
                },
            }
            for page in successful_pages
        ],
    }

    return f"""You are an expert at writing llms.txt files for businesses and websites.

Generate a valid llms.txt file using ONLY the extracted website data below.
Do not invent facts, phone numbers, addresses, services, or URLs.

Required llms.txt structure:
1. H1 title: # {business_name}
2. Blockquote summary on the next line: > one concise sentence about the business
3. Optional short Markdown paragraph with helpful context
4. H2 sections with Markdown link lists, for example:
   ## Main Pages
   - [Page Title](https://example.com/page): short useful description
5. Use absolute URLs only
6. Add ## Contact when phone/email exists in business_info
7. Put lower-priority pages under ## Optional if needed

Rules:
- Use title, headings, description, and content from each page
- Use business_info for contact details only when present
- Keep descriptions short and useful for AI systems
- Do not wrap output in code fences
- Return only the llms.txt Markdown content

Extracted website data (JSON):
{json.dumps(payload, ensure_ascii=False, indent=2)}"""


def _generate_with_openai(prompt: str) -> str:
    """
    STEP 4: Ask OpenAI to write the final llms.txt Markdown text.
    Tries multiple models if the first one fails.
    """
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    last_error: Exception | None = None

    for model in LLM_MODEL_FALLBACKS:
        try:
            logger.info(f"llms_txt_generator: calling OpenAI model='{model}'")
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You write accurate llms.txt files in Markdown. "
                            "Never invent unsupported business facts."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
            )
            content = (response.choices[0].message.content or "").strip()
            if not content:
                raise ValueError("OpenAI returned empty llms.txt content")

            content = content.strip()
            # Strip markdown code fences if the model wrapped the output.
            if content.startswith("```"):
                content = content.strip("`")
                if content.lower().startswith("markdown"):
                    content = content[8:].strip()

            if not content.startswith("#"):
                raise ValueError("Generated content does not start with an H1 heading")

            logger.info(
                f"llms_txt_generator: OpenAI model='{model}' generated "
                f"{len(content)} character(s)"
            )
            return content
        except Exception as exc:
            last_error = exc
            logger.warning(
                f"llms_txt_generator: OpenAI model='{model}' failed — {exc}"
            )

    raise RuntimeError(f"Failed to generate llms.txt — {last_error}")


def _save_outputs(
    website_url: str,
    business_id: str | int | None,
    llms_txt_content: str,
    metadata: dict,
) -> dict:
    """
    STEP 5: Save llms.txt and generation_metadata.json to llms_txt_outputs/.
    """
    folder = get_llms_txt_output_folder(website_url, business_id)
    folder.mkdir(parents=True, exist_ok=True)

    llms_path = get_llms_txt_output_path(website_url, business_id)
    metadata_path = get_llms_txt_metadata_path(website_url, business_id)

    # Write via temp file first so we never leave a half-written file.
    temp_path = llms_path.with_suffix(".txt.tmp")
    with open(temp_path, "w", encoding="utf-8") as file:
        file.write(llms_txt_content)
    os.replace(temp_path, llms_path)

    with open(metadata_path, "w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=2, ensure_ascii=False)

    logger.info(
        f"llms_txt_generator: saved llms.txt to '{llms_path}' and "
        f"metadata to '{metadata_path}'"
    )

    return {
        "local_folder": str(folder),
        "llms_txt_path": str(llms_path),
        "metadata_path": str(metadata_path),
    }


async def generate_llms_txt(payload: LlmsTxtGeneratorRequest) -> dict:
    """
    Main pipeline function: crawl → filter → extract → LLM → save → return result.

    Called by the API route (sync or background job).
    """
    _log_section("LLMS.txt generation started")
    logger.info(
        f"llms_txt_generator: website_url='{payload.website_url}', "
        f"business_name='{payload.business_name}', "
        f"business_id='{payload.business_id}'"
    )

    # Step 1: recursively crawl the site to find internal page URLs.
    base_url, discovered_urls = discover_website_urls(payload.website_url)
    # Step 2: keep only the best URLs for llms.txt.
    selected_urls = filter_and_rank_urls(base_url, discovered_urls)
    # Step 3: download and read each selected page.
    extracted_pages = await extract_pages_async(selected_urls)

    successful_pages = [
        page for page in extracted_pages if page.get("status") == "success"
    ]
    if not successful_pages:
        raise RuntimeError(
            "No pages could be extracted successfully. "
            "Check the website URL and try again."
        )

    business_name = _infer_business_name(
        base_url,
        payload.business_name,
        successful_pages,
    )

    # Step 4: OpenAI writes the llms.txt file from extracted data.
    _log_section("Step 4 — LLM agent generation")
    prompt = _build_generation_prompt(business_name, base_url, extracted_pages)
    llms_txt_content = _generate_with_openai(prompt)

    # Keep a full audit trail of what was crawled and extracted.
    metadata = {
        "generated_at": datetime.now().strftime("%d %B %Y %H:%M"),
        "website_url": base_url,
        "business_name": business_name,
        "business_id": payload.business_id,
        "discovered_url_count": len(discovered_urls),
        "selected_url_count": len(selected_urls),
        "extracted_page_count": len(extracted_pages),
        "successful_page_count": len(successful_pages),
        "discovered_urls": discovered_urls,
        "selected_urls": selected_urls,
        "extracted_pages": extracted_pages,
    }

    # Step 5: save files locally.
    storage = _save_outputs(
        base_url,
        payload.business_id,
        llms_txt_content,
        metadata,
    )

    result = {
        "status": "success",
        "website_url": base_url,
        "business_name": business_name,
        "business_id": payload.business_id,
        "llms_txt_url": f"{base_url}/llms.txt",
        "discovered_url_count": len(discovered_urls),
        "selected_url_count": len(selected_urls),
        "successful_page_count": len(successful_pages),
        "llms_txt_content": llms_txt_content,
        "storage": storage,
        "message": (
            "llms.txt generated successfully. Upload the file to your website root "
            f"at {base_url}/llms.txt to make it publicly available."
        ),
    }

    _log_section("LLMS.txt generation completed")
    logger.info(
        f"llms_txt_generator: completed for '{business_name}' — "
        f"{len(llms_txt_content)} chars, "
        f"{len(successful_pages)}/{len(selected_urls)} pages extracted"
    )
    return result


async def generate_llms_txt_from_urls(payload: LlmsTxtFromUrlsRequest) -> dict:
    """
    Extract content from selected URLs (8 async workers) then generate llms.txt via OpenAI.
    """

    _log_section("LLMS.txt generation from URLs")
    base_url = normalize_website_url(payload.website_url)
    selected_urls = list(dict.fromkeys(url.strip() for url in payload.urls if url.strip()))

    logger.info(
        f"llms_txt_generator: website_url='{base_url}', "
        f"urls={len(selected_urls)}, max_workers={payload.max_workers}"
    )

    extracted_pages = await extract_pages_async(
        selected_urls,
        max_workers=payload.max_workers,
    )

    successful_pages = [
        page for page in extracted_pages if page.get("status") == "success"
    ]
    if not successful_pages:
        raise RuntimeError(
            "No pages could be extracted successfully. Check the URLs and try again."
        )

    business_name = _infer_business_name(
        base_url,
        payload.business_name,
        successful_pages,
    )

    _log_section("Step 4 — LLM generation (gpt-4o-mini)")
    prompt = _build_generation_prompt(business_name, base_url, extracted_pages)
    llms_txt_content = _generate_with_openai(prompt)

    metadata = {
        "generated_at": datetime.now().strftime("%d %B %Y %H:%M"),
        "website_url": base_url,
        "business_name": business_name,
        "business_id": payload.business_id,
        "selected_url_count": len(selected_urls),
        "extracted_page_count": len(extracted_pages),
        "successful_page_count": len(successful_pages),
        "selected_urls": selected_urls,
        "extracted_pages": extracted_pages,
    }

    storage = _save_outputs(
        base_url,
        payload.business_id,
        llms_txt_content,
        metadata,
    )

    return {
        "status": "success",
        "website_url": base_url,
        "business_name": business_name,
        "business_id": payload.business_id,
        "llms_txt_url": f"{base_url}/llms.txt",
        "selected_url_count": len(selected_urls),
        "successful_page_count": len(successful_pages),
        "extracted_pages": extracted_pages,
        "llms_txt_content": llms_txt_content,
        "storage": storage,
        "message": (
            "llms.txt generated successfully. Upload the file to your website root "
            f"at {base_url}/llms.txt to make it publicly available."
        ),
    }
