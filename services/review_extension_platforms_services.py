import asyncio
import json
import uuid
from datetime import datetime
from urllib.parse import urlparse

import httpx

from schema.review_extension_platforms_schema import ReviewExtensionPlatformsRequest
from services.logger_services import logger
from services.s3_service import load_scraped_result_data
from services.scrapper_services import (
    _run_actor_with_retry,
    client,
    get_dataset_id,
    save_scraped_result,
)
from utils.scraped_result_paths import (
    build_scrape_storage_slug,
    get_scraped_result_path,
)

FACEBOOK_REVIEWS_ACTOR = "premiumscraper/facebook-reviews-scraper"
TRUSTPILOT_REVIEWS_ACTOR = "shahidirfan/trustpilot-reviews-scraper"
TRUSTPILOT_REVIEWS_FALLBACK_ACTOR = "automation-lab/trustpilot"
FEEFO_REVIEWS_API_BASE = "https://api.feefo.com/api/20/reviews/all"
FEEFO_API_PAGE_SIZE = 100
LOG_TAG = "review_extension_platforms"
EXTENSION_PLATFORMS = ("facebook", "trustpilot", "feefo")


def _normalize_facebook_reviews_url(url: str) -> str:
    url = url.strip().rstrip("/")
    url = url.replace("://web.facebook.com", "://www.facebook.com")
    if not url.lower().endswith("/reviews"):
        url = f"{url}/reviews"
    return url


def _normalize_trustpilot_url(url: str) -> str:
    """Accept full Trustpilot review URL or bare domain."""
    raw = url.strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    path = (parsed.path or "").strip("/")

    if "trustpilot." in (parsed.netloc or "").lower() and path.lower().startswith(
        "review/"
    ):
        domain = path.split("/", 1)[1].strip("/")
        if domain:
            return f"https://www.trustpilot.com/review/{domain}"

    if parsed.netloc and "trustpilot." not in parsed.netloc.lower():
        host = parsed.netloc.lower().removeprefix("www.")
        return f"https://www.trustpilot.com/review/{host}"

    return raw.rstrip("/")


def _normalize_feefo_url(url: str) -> str:
    """Keep Feefo brand review URL; strip query params."""
    raw = url.strip()
    parsed = urlparse(raw)
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    return clean


def _extract_feefo_merchant_identifier(feefo_url: str) -> str:
    """
    Extract Feefo merchant_identifier from a public reviews URL.
    e.g. https://www.feefo.com/en-GB/reviews/tesco-mobile -> tesco-mobile
    """
    parsed = urlparse(feefo_url.strip())
    parts = [p for p in (parsed.path or "").split("/") if p]
    try:
        reviews_idx = next(
            i for i, part in enumerate(parts) if part.lower() == "reviews"
        )
    except StopIteration:
        return ""

    if reviews_idx + 1 >= len(parts):
        return ""
    return parts[reviews_idx + 1].strip()


def _first_non_empty(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _extract_facebook_owner_reply(item: dict) -> tuple[str | None, str | None]:
    """
    Pull page owner reply from premiumscraper comments tree.
    Owner comments usually match page facebookId / display name.
    """
    page_id = str(
        item.get("facebookId")
        or (item.get("sourceDetails") or {}).get("facebookId")
        or ""
    ).strip()
    page_names = {
        str(v).strip().lower()
        for v in (
            item.get("pageName"),
            (item.get("sourceDetails") or {}).get("pageName"),
            (item.get("sourceDetails") or {}).get("pageDisplayName"),
            (item.get("sourceDetails") or {}).get("pageUsername"),
        )
        if v
    }

    comments_block = item.get("comments") or {}
    top_comments = comments_block.get("top_level_comments") or []
    if not isinstance(top_comments, list):
        return None, None

    for comment in top_comments:
        if not isinstance(comment, dict):
            continue
        author = comment.get("author") or {}
        author_id = str(author.get("profile_id") or "").strip()
        author_name = str(author.get("profile_name") or "").strip().lower()
        is_owner = False
        if page_id and author_id and author_id == page_id:
            is_owner = True
        elif author_name and author_name in page_names:
            is_owner = True
        if not is_owner:
            continue

        reply_text = comment.get("message_text")
        if isinstance(reply_text, str) and reply_text.strip():
            return reply_text.strip(), comment.get("created_at")
    return None, None


def _map_facebook_review(item: dict) -> dict:
    source = item.get("sourceDetails") or {}
    page_id = item.get("facebookId") or source.get("facebookId") or ""
    page_name = (
        source.get("pageDisplayName")
        or source.get("pageName")
        or item.get("pageName")
        or "N/A"
    )
    page_pic = source.get("pageImage")
    if not page_pic and page_id:
        page_pic = (
            f"https://lookaside.fbsbx.com/lookaside/crawler/media/?media_id={page_id}"
        )

    owner_reply, owner_reply_date = _extract_facebook_owner_reply(item)
    user = item.get("user") or {}

    return {
        "pageName": page_name,
        "pageId": str(page_id) if page_id else None,
        "pageProfilePicture": page_pic,
        "reviewId": str(item.get("legacyId") or item.get("id") or ""),
        "reviewUrl": item.get("url"),
        "authorName": user.get("name") or "Anonymous",
        "isRecommended": item.get("isRecommended"),
        "text": item.get("text") or "",
        "publishedDate": item.get("date"),
        "likesCount": item.get("likesCount"),
        "commentsCount": item.get("commentsCount"),
        "tags": item.get("tags") or [],
        "owner_reply": owner_reply,
        "owner_reply_date": owner_reply_date,
    }


def _map_trustpilot_review(item: dict) -> dict:
    """Map Trustpilot review from primary or fallback actor into a shared shape."""
    review_id = _first_non_empty(
        item.get("reviewId"),
        item.get("review_id"),
        item.get("id"),
        "",
    )
    review_url = _first_non_empty(item.get("reviewUrl"), item.get("review_url"))
    if not review_url and review_id:
        review_url = f"https://www.trustpilot.com/reviews/{review_id}"

    owner_reply = _first_non_empty(
        item.get("replyMessage"),
        item.get("company_reply_text"),
        item.get("owner_reply"),
    )
    if isinstance(owner_reply, str):
        owner_reply = owner_reply.strip() or None
    else:
        owner_reply = None

    owner_reply_date = _first_non_empty(
        item.get("replyPublishedDate"),
        item.get("company_reply_date"),
        item.get("owner_reply_date"),
    )

    verification = _first_non_empty(
        item.get("verificationLevel"),
        item.get("verification_level"),
    )
    if not verification:
        if item.get("is_verified") or item.get("isVerified"):
            verification = "verified"
        else:
            verification = "not-verified"

    return {
        "reviewId": str(review_id),
        "authorName": (
            item.get("authorName")
            or item.get("reviewer_name")
            or "Anonymous"
        ),
        "rating": item.get("rating"),
        "title": item.get("title") or "",
        "text": item.get("text") or "",
        "publishedDate": _first_non_empty(
            item.get("publishedDate"),
            item.get("published_date"),
        ),
        "country": _first_non_empty(
            item.get("country"),
            item.get("reviewer_country"),
        ),
        "language": item.get("language"),
        "verificationLevel": verification,
        "reviewUrl": review_url,
        "owner_reply": owner_reply,
        "owner_reply_date": owner_reply_date,
    }


def _feefo_owner_reply_from_thread(thread: list | None) -> tuple[str | None, str | None]:
    """Pick first merchant reply from a Feefo service/product thread."""
    if not isinstance(thread, list):
        return None, None
    for entry in thread:
        if not isinstance(entry, dict):
            continue
        entry_type = str(entry.get("type") or "").upper()
        if entry_type != "MERCHANT_COMMENT":
            continue
        comment = entry.get("comment")
        if isinstance(comment, str) and comment.strip():
            return comment.strip(), entry.get("created_at")
    return None, None


def _map_feefo_review(item: dict) -> dict:
    """Map one Feefo Reviews API row into our shared review shape."""
    service = item.get("service") if isinstance(item.get("service"), dict) else {}
    products = item.get("products") if isinstance(item.get("products"), list) else []
    first_product = products[0] if products and isinstance(products[0], dict) else {}
    customer = item.get("customer") if isinstance(item.get("customer"), dict) else {}

    owner_reply, owner_reply_date = _feefo_owner_reply_from_thread(
        service.get("thread")
    )
    if not owner_reply:
        owner_reply, owner_reply_date = _feefo_owner_reply_from_thread(
            first_product.get("thread")
        )

    review_id = _first_non_empty(
        service.get("id"),
        first_product.get("id"),
        "",
    )
    text = _first_non_empty(
        service.get("review"),
        first_product.get("review"),
        "",
    )
    title = _first_non_empty(service.get("title"), first_product.get("title"), "")
    rating = _first_non_empty(
        (service.get("rating") or {}).get("rating")
        if isinstance(service.get("rating"), dict)
        else None,
        (first_product.get("rating") or {}).get("rating")
        if isinstance(first_product.get("rating"), dict)
        else None,
    )
    published_date = _first_non_empty(
        service.get("created_at"),
        first_product.get("created_at"),
        item.get("last_updated_date"),
    )
    product_info = (
        first_product.get("product")
        if isinstance(first_product.get("product"), dict)
        else {}
    )

    return {
        "reviewId": str(review_id) if review_id else "",
        "authorName": customer.get("display_name") or "Anonymous",
        "rating": rating,
        "title": title or "",
        "text": text or "",
        "publishedDate": published_date,
        "country": customer.get("display_location"),
        "productName": product_info.get("title"),
        "productSku": product_info.get("sku"),
        "reviewUrl": item.get("url"),
        "serviceRating": (
            (service.get("rating") or {}).get("rating")
            if isinstance(service.get("rating"), dict)
            else None
        ),
        "serviceComment": service.get("review") or "",
        "productRating": (
            (first_product.get("rating") or {}).get("rating")
            if isinstance(first_product.get("rating"), dict)
            else None
        ),
        "productComment": first_product.get("review") or "",
        "owner_reply": owner_reply,
        "owner_reply_date": owner_reply_date,
    }


def _skipped_platform(platform: str, url_key: str) -> dict:
    return {
        "status": f"⏭️ Skipped — no {platform} URL provided",
        url_key: None,
        "total_reviews": 0,
        "reviews": [],
    }


def _scrape_facebook_reviews(facebook_url: str, max_reviews: int) -> dict:
    url = _normalize_facebook_reviews_url(facebook_url)
    logger.info(
        f"{LOG_TAG}: starting actor='{FACEBOOK_REVIEWS_ACTOR}' "
        f"url='{url}' max_reviews={max_reviews}"
    )

    try:
        run = _run_actor_with_retry(
            FACEBOOK_REVIEWS_ACTOR,
            {
                "facebook_urls": [{"url": url}],
                "max_reviews": max_reviews,
                "include_review_comments": True,
                "comments_limit": 10,
                "include_comment_replies": True,
                "comment_replies_limit": 5,
                "comment_filter": "most_relevant",
                "proxyCountry": "US",
            },
        )
        items = list(client.dataset(get_dataset_id(run)).list_items().items)
        # Actor may also emit non-review diagnostic rows — keep review-shaped items only.
        review_items = [
            item
            for item in items
            if isinstance(item, dict)
            and (item.get("type") == "review" or item.get("text") is not None)
        ]
        reviews = [_map_facebook_review(item) for item in review_items[:max_reviews]]

        status = f"✅ Success — {len(reviews)} reviews fetched"
        logger.info(f"{LOG_TAG} [facebook]: {status}")
        return {
            "status": status,
            "facebook_url": url,
            "total_reviews": len(reviews),
            "reviews": reviews,
        }
    except Exception as e:
        logger.exception(f"{LOG_TAG} [facebook]: failed — {e}")
        return {
            "status": f"❌ Failed: {str(e)}",
            "facebook_url": url,
            "total_reviews": 0,
            "reviews": [],
        }


def _run_trustpilot_actor(
    actor_id: str,
    actor_input: dict,
    url: str,
    max_reviews: int,
) -> list[dict]:
    logger.info(
        f"{LOG_TAG}: starting actor='{actor_id}' "
        f"url='{url}' max_reviews={max_reviews}"
    )
    run = _run_actor_with_retry(actor_id, actor_input)
    items = list(client.dataset(get_dataset_id(run)).list_items().items)
    review_items = [
        item
        for item in items
        if isinstance(item, dict)
        and (
            item.get("text") is not None
            or item.get("title") is not None
            or item.get("rating") is not None
        )
    ]
    return [_map_trustpilot_review(item) for item in review_items[:max_reviews]]


def _scrape_trustpilot_reviews(trustpilot_url: str, max_reviews: int) -> dict:
    url = _normalize_trustpilot_url(trustpilot_url)
    primary_error: BaseException | None = None

    # 1) Primary actor
    try:
        reviews = _run_trustpilot_actor(
            TRUSTPILOT_REVIEWS_ACTOR,
            {
                "urls": [url],
                "results_wanted": max_reviews,
                "max_pages": max(1, (max_reviews + 19) // 20),
                "sort": "recency",
                "languages": "all",
                "verified": False,
            },
            url,
            max_reviews,
        )
        if reviews:
            status = (
                f"✅ Success — {len(reviews)} reviews fetched "
                f"(actor={TRUSTPILOT_REVIEWS_ACTOR})"
            )
            logger.info(f"{LOG_TAG} [trustpilot]: {status}")
            return {
                "status": status,
                "trustpilot_url": url,
                "actor_used": TRUSTPILOT_REVIEWS_ACTOR,
                "total_reviews": len(reviews),
                "reviews": reviews,
            }
        logger.warning(
            f"{LOG_TAG} [trustpilot]: primary actor='{TRUSTPILOT_REVIEWS_ACTOR}' "
            "returned 0 reviews — trying fallback"
        )
    except Exception as e:
        primary_error = e
        logger.exception(
            f"{LOG_TAG} [trustpilot]: primary actor='{TRUSTPILOT_REVIEWS_ACTOR}' "
            f"failed — {e}; trying fallback='{TRUSTPILOT_REVIEWS_FALLBACK_ACTOR}'"
        )

    # 2) Fallback: automation-lab/trustpilot
    try:
        reviews = _run_trustpilot_actor(
            TRUSTPILOT_REVIEWS_FALLBACK_ACTOR,
            {
                "companyUrls": [url],
                "maxReviewsPerCompany": max_reviews,
                "sort": "recency",
                "includeCompanyInfo": False,
            },
            url,
            max_reviews,
        )
        status = (
            f"✅ Success — {len(reviews)} reviews fetched "
            f"(fallback actor={TRUSTPILOT_REVIEWS_FALLBACK_ACTOR})"
        )
        logger.info(f"{LOG_TAG} [trustpilot]: {status}")
        return {
            "status": status,
            "trustpilot_url": url,
            "actor_used": TRUSTPILOT_REVIEWS_FALLBACK_ACTOR,
            "total_reviews": len(reviews),
            "reviews": reviews,
        }
    except Exception as e:
        logger.exception(
            f"{LOG_TAG} [trustpilot]: fallback actor="
            f"'{TRUSTPILOT_REVIEWS_FALLBACK_ACTOR}' failed — {e}"
        )
        detail = str(primary_error) if primary_error else str(e)
        return {
            "status": f"❌ Failed: {detail}",
            "trustpilot_url": url,
            "actor_used": None,
            "total_reviews": 0,
            "reviews": [],
        }


def _feefo_api_error_detail(response: httpx.Response) -> str:
    """Turn Feefo API error JSON into a short status message."""
    try:
        payload = response.json()
    except Exception:
        payload = None

    message = None
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, list):
            message = "; ".join(str(part) for part in message if part)
        elif message is not None:
            message = str(message).strip()
            # Feefo sometimes wraps the real text as a JSON-array string.
            if message.startswith("[") and message.endswith("]"):
                try:
                    parsed = json.loads(message)
                    if isinstance(parsed, list):
                        message = "; ".join(str(part) for part in parsed if part)
                except Exception:
                    pass

    if message:
        return message
    return f"HTTP {response.status_code}"


def _scrape_feefo_reviews(feefo_url: str, max_reviews: int) -> dict:
    """Fetch Feefo reviews + merchant replies via public Reviews API (no Apify)."""
    url = _normalize_feefo_url(feefo_url)
    merchant_id = _extract_feefo_merchant_identifier(url)
    if not merchant_id:
        status = "❌ Failed: could not extract Feefo merchant_identifier from URL"
        logger.error(f"{LOG_TAG} [feefo]: {status} url='{url}'")
        return {
            "status": status,
            "feefo_url": url,
            "merchant_identifier": None,
            "total_reviews": 0,
            "reviews": [],
        }

    logger.info(
        f"{LOG_TAG}: starting Feefo Reviews API "
        f"merchant='{merchant_id}' url='{url}' max_reviews={max_reviews}"
    )

    reviews: list[dict] = []
    page = 1
    try:
        with httpx.Client(timeout=60.0, follow_redirects=True) as http:
            while len(reviews) < max_reviews:
                params = {
                    "merchant_identifier": merchant_id,
                    "full_thread": "include",
                    "since_period": "all",
                    "page_size": min(FEEFO_API_PAGE_SIZE, max_reviews - len(reviews)),
                    "page": page,
                }
                response = http.get(FEEFO_REVIEWS_API_BASE, params=params)
                if response.is_error:
                    detail = _feefo_api_error_detail(response)
                    status = f"❌ Failed: {detail}"
                    logger.error(
                        f"{LOG_TAG} [feefo]: {status} "
                        f"http={response.status_code} merchant='{merchant_id}'"
                    )
                    return {
                        "status": status,
                        "feefo_url": url,
                        "merchant_identifier": merchant_id,
                        "total_reviews": 0,
                        "reviews": [],
                    }

                payload = response.json()
                items = payload.get("reviews") or []
                if not items:
                    break

                for item in items:
                    if not isinstance(item, dict):
                        continue
                    reviews.append(_map_feefo_review(item))
                    if len(reviews) >= max_reviews:
                        break

                summary = payload.get("summary") or {}
                meta = (
                    summary.get("meta")
                    if isinstance(summary.get("meta"), dict)
                    else summary
                )
                current_page = int(
                    meta.get("current_page") or meta.get("page") or page
                )
                pages = int(meta.get("pages") or current_page)
                if current_page >= pages:
                    break
                page = current_page + 1

        status = f"✅ Success — {len(reviews)} reviews fetched"
        logger.info(f"{LOG_TAG} [feefo]: {status}")
        return {
            "status": status,
            "feefo_url": url,
            "merchant_identifier": merchant_id,
            "total_reviews": len(reviews),
            "reviews": reviews,
        }
    except Exception as e:
        logger.exception(f"{LOG_TAG} [feefo]: failed — {e}")
        return {
            "status": f"❌ Failed: {str(e)}",
            "feefo_url": url,
            "merchant_identifier": merchant_id,
            "total_reviews": 0,
            "reviews": [],
        }


def _assign_extension_review_uuids(platform_block: dict) -> dict:
    """Add UUID + template_id + AI_Draft to each review, same pattern as google_maps/yelp/tripadvisor."""
    reviews = platform_block.get("reviews") or []
    for index, review in enumerate(reviews):
        cleaned = {
            key: value
            for key, value in review.items()
            if key not in ("UUID", "template_id", "AI_Draft")
        }
        reviews[index] = {
            "UUID": str(uuid.uuid4()),
            "template_id": None,
            **cleaned,
            "AI_Draft": None,
        }
    platform_block["reviews"] = reviews
    platform_block["total_reviews"] = len(reviews)
    return platform_block


def _load_existing_scraped_result(
    business_name: str,
    business_id: str | int | None,
) -> dict:
    try:
        return load_scraped_result_data(business_name, business_id)
    except FileNotFoundError:
        logger.info(
            f"{LOG_TAG}: no existing scraped_result.json for "
            f"business='{business_name}', business_id='{business_id}' — creating new"
        )
        return {
            "business": business_name.strip(),
            "business_id": business_id,
            "scraped_at": datetime.now().strftime("%d %B %Y %H:%M"),
            "summary": {},
        }
    except Exception as e:
        logger.warning(
            f"{LOG_TAG}: could not load existing scraped_result.json — {e}; "
            "starting fresh merge base"
        )
        return {
            "business": business_name.strip(),
            "business_id": business_id,
            "scraped_at": datetime.now().strftime("%d %B %Y %H:%M"),
            "summary": {},
        }


def _merge_extension_platforms_into_scraped_result(
    existing: dict,
    platforms: dict[str, dict],
    requested: set[str],
    business_name: str,
    business_id: str | int | None,
) -> dict:
    """
    Merge facebook / trustpilot / feefo into scraped_result.json after tripadvisor.
    Only requested platforms overwrite existing data.
    """
    existing = dict(existing or {})
    existing["business_id"] = (
        business_id if business_id is not None else existing.get("business_id")
    )
    if not existing.get("business"):
        existing["business"] = business_name.strip()
    existing["extension_scraped_at"] = datetime.now().strftime("%d %B %Y %H:%M")

    summary = dict(existing.get("summary") or {})
    for platform in requested:
        block = _assign_extension_review_uuids(dict(platforms[platform]))
        platforms[platform] = block
        summary[platform] = block.get("status")
        existing[platform] = block

    existing["summary"] = summary

    # Rebuild key order: keep core fields, then google/yelp/tripadvisor, then extensions
    ordered: dict = {}
    leading_keys = (
        "business",
        "business_id",
        "branch_name",
        "google_place_id",
        "scraped_at",
        "extension_scraped_at",
        "summary",
        "google_maps",
        "yelp",
        "tripadvisor",
        "facebook",
        "trustpilot",
        "feefo",
    )
    for key in leading_keys:
        if key in existing:
            ordered[key] = existing[key]
    for key, value in existing.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


async def scrape_review_extension_platforms(
    payload: ReviewExtensionPlatformsRequest,
) -> dict:
    max_reviews = payload.max_reviews
    business_name = payload.business_name.strip()
    business_id = payload.business_id
    tasks: dict[str, asyncio.Task] = {}
    requested: set[str] = set()

    if payload.facebook_url:
        requested.add("facebook")
        tasks["facebook"] = asyncio.create_task(
            asyncio.to_thread(
                _scrape_facebook_reviews,
                str(payload.facebook_url),
                max_reviews,
            )
        )
    if payload.trustpilot_url:
        requested.add("trustpilot")
        tasks["trustpilot"] = asyncio.create_task(
            asyncio.to_thread(
                _scrape_trustpilot_reviews,
                str(payload.trustpilot_url),
                max_reviews,
            )
        )
    if payload.feefo_url:
        requested.add("feefo")
        tasks["feefo"] = asyncio.create_task(
            asyncio.to_thread(
                _scrape_feefo_reviews,
                str(payload.feefo_url),
                max_reviews,
            )
        )

    logger.info(
        f"{LOG_TAG}: running platforms in parallel — "
        f"business='{business_name}', business_id='{business_id}', "
        f"platforms={list(tasks.keys())}, max_reviews={max_reviews}"
    )

    platforms = {
        "facebook": _skipped_platform("Facebook", "facebook_url"),
        "trustpilot": _skipped_platform("Trustpilot", "trustpilot_url"),
        "feefo": _skipped_platform("Feefo", "feefo_url"),
    }
    url_keys = {
        "facebook": "facebook_url",
        "trustpilot": "trustpilot_url",
        "feefo": "feefo_url",
    }
    payload_urls = {
        "facebook": payload.facebook_url,
        "trustpilot": payload.trustpilot_url,
        "feefo": payload.feefo_url,
    }

    if tasks:
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for platform, result in zip(tasks.keys(), results):
            if isinstance(result, BaseException):
                logger.exception(f"{LOG_TAG} [{platform}]: task failed — {result}")
                platforms[platform] = {
                    "status": f"❌ Failed: {str(result)}",
                    url_keys[platform]: (
                        str(payload_urls[platform]) if payload_urls[platform] else None
                    ),
                    "total_reviews": 0,
                    "reviews": [],
                }
            else:
                platforms[platform] = result

    existing = _load_existing_scraped_result(business_name, business_id)
    merged = _merge_extension_platforms_into_scraped_result(
        existing=existing,
        platforms=platforms,
        requested=requested,
        business_name=business_name,
        business_id=business_id,
    )
    save_scraped_result(merged, business_name, business_id)

    storage_slug = build_scrape_storage_slug(business_name, business_id)
    result_path = str(get_scraped_result_path(business_name, business_id))
    logger.info(
        f"{LOG_TAG}: saved extension platforms into scraped_result.json — "
        f"path='{result_path}'"
    )

    return {
        "business_name": business_name,
        "business_id": business_id,
        "storage_folder": storage_slug,
        "scraped_result_path": result_path,
        "summary": {
            platform: platforms[platform]["status"] for platform in EXTENSION_PLATFORMS
        },
        "facebook": platforms["facebook"],
        "trustpilot": platforms["trustpilot"],
        "feefo": platforms["feefo"],
    }
