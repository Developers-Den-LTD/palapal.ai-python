import asyncio
from datetime import datetime
from pathlib import Path
import json
import os
import re
import shutil
import time

from apify_client import ApifyClient

from core.config import settings
from schema.scrapper import ScrapeRequest
from services.logger_services import logger
from services.s3_service import (
    delete_scraped_result_from_s3,
    get_s3_key,
    upload_scraped_result_to_s3,
)
from utils.platform_urls import normalize_tripadvisor_url, normalize_yelp_url
from utils.scraped_result_paths import (
    get_scraped_result_folder,
    get_scraped_result_path,
    slugify_folder_name,
)

APIFY_API_TOKEN = settings.Apify_API
client = ApifyClient(APIFY_API_TOKEN)

ACTOR_MAX_ATTEMPTS = 3
ACTOR_RETRY_DELAY_SECONDS = 2


def _run_actor_with_retry(actor_id: str, run_input: dict):
    last_error = None
    for attempt in range(1, ACTOR_MAX_ATTEMPTS + 1):
        try:
            logger.info(
                f"run_actor_with_retry: actor='{actor_id}' "
                f"attempt {attempt}/{ACTOR_MAX_ATTEMPTS}"
            )
            return client.actor(actor_id).call(run_input=run_input)
        except Exception as e:
            last_error = e
            logger.warning(
                f"run_actor_with_retry: actor='{actor_id}' "
                f"attempt {attempt} failed — {e}"
            )
            if attempt < ACTOR_MAX_ATTEMPTS:
                time.sleep(ACTOR_RETRY_DELAY_SECONDS)
    logger.exception(
        f"run_actor_with_retry: actor='{actor_id}' failed after "
        f"{ACTOR_MAX_ATTEMPTS} attempts"
    )
    raise last_error


def save_scraped_result(
    data: dict,
    business_name: str,
    business_id: str | int | None = None,
) -> None:
    try:
        business_name = business_name.strip()
        # scraping_results/<business_slug>[_business_id]/scraped_result.json
        result_path = get_scraped_result_path(business_name, business_id)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = result_path.with_suffix(".json.tmp")

        json_text = json.dumps(data, indent=2, ensure_ascii=False)
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(json_text)

        os.replace(temp_path, result_path)
        logger.info(f"save_scraped_result: saved locally to {result_path}")

        if not upload_scraped_result_to_s3(business_name, result_path, business_id):
            logger.error(
                f"save_scraped_result: S3 upload failed for '{business_name}' "
                f"(local file kept at {result_path})"
            )
        else:
            logger.info(
                f"save_scraped_result: mirrored to S3 for '{business_name}' "
                f"at scraping_results/{result_path.parent.name}/scraped_result.json"
            )

    except (OSError, TypeError, ValueError) as e:
        if 'temp_path' in locals() and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        logger.exception(f"save_scraped_result: failed to write result for {business_name} — {e}")
        raise


def _delete_scraped_result_local(business_name: str) -> dict:
    business_name = business_name.strip()
    folder_path = get_scraped_result_folder(business_name)
    result = {
        "existed": False,
        "deleted": False,
        "error": None,
        "local_path": str(folder_path),
    }

    if not folder_path.exists():
        logger.info(
            f"delete_scraped_data: no local folder for business='{business_name}' — {folder_path}"
        )
        return result

    result["existed"] = True
    try:
        shutil.rmtree(folder_path)
        result["deleted"] = True
        logger.info(
            f"delete_scraped_data: deleted local folder for business='{business_name}' — {folder_path}"
        )
        return result
    except OSError as exc:
        error_message = f"failed to delete local folder '{folder_path}' — {exc}"
        logger.error(f"delete_scraped_data: {error_message}")
        result["error"] = error_message
        return result


def delete_scraped_data(business_name: str) -> dict:
    business_name = business_name.strip()
    folder_slug = slugify_folder_name(business_name)
    s3_key = get_s3_key(business_name)

    logger.info(f"delete_scraped_data: request received business='{business_name}'")

    local_result = _delete_scraped_result_local(business_name)
    s3_result = delete_scraped_result_from_s3(business_name)

    base_response = {
        "business_name": business_name,
        "folder_slug": folder_slug,
        "local_path": local_result["local_path"],
        "s3_key": s3_key,
        "deleted": {
            "local": local_result["deleted"],
            "s3": s3_result["deleted"],
        },
        "found": {
            "local": local_result["existed"],
            "s3": s3_result["existed"],
        },
    }

    errors = [
        error
        for error in (local_result.get("error"), s3_result.get("error"))
        if error
    ]
    if errors:
        return {
            **base_response,
            "status": "error",
            "message": "; ".join(errors),
        }

    if not local_result["existed"] and not s3_result["existed"]:
        return {
            **base_response,
            "status": "error",
            "message": f"No scraped data found for '{business_name}'.",
        }

    deleted_locations = []
    if local_result["deleted"]:
        deleted_locations.append("local storage")
    if s3_result["deleted"]:
        deleted_locations.append("AWS S3")

    return {
        **base_response,
        "status": "success",
        "message": (
            f"Scraped data deleted for '{business_name}' from "
            f"{', '.join(deleted_locations)}."
        ),
    }


def get_dataset_id(run):
    dataset_id = (
        getattr(run, "default_dataset_id", None)
        or (run.get("defaultDatasetId") if isinstance(run, dict) else None)
    )
    if not dataset_id:
        logger.warning("get_dataset_id: no dataset ID found in Apify run response")
    return dataset_id


def format_date(date):
    if not date:
        return None
    try:
        return datetime.strptime(str(date)[:10], "%Y-%m-%d").strftime("%d %B %Y")
    except (ValueError, TypeError) as e:
        logger.debug(f"format_date: could not parse date '{date}' — {e}")
        return date


def extract_nap(items):
    for item in items:
        name = item.get("name")
        phone = item.get("phone")
        address_parts = [
            item.get("address1"),
            item.get("city"),
            item.get("zip"),
            item.get("country"),
        ]
        address = ", ".join([part for part in address_parts if part])
        if name or phone or address:
            return {
                "name": name or "N/A",
                "phone": phone or "N/A",
                "address": address or "N/A",
            }
    return {"name": "N/A", "phone": "N/A", "address": "N/A"}


def extract_yelp_reviews(items):
    reviews = []
    for item in items:
        comment_text = item.get("text")
        if isinstance(comment_text, dict):
            comment_text = comment_text.get("full")
        comment_text = (
            comment_text or item.get("comment") or item.get("reviewText")
        )

        raw_rating = item.get("rating") or item.get("stars")
        if not comment_text and raw_rating is None:
            continue

        rating = raw_rating
        if isinstance(raw_rating, (int, float)) and raw_rating > 5:
            rating = raw_rating / 10

        author_name = "Anonymous"
        author_obj = item.get("author")
        if isinstance(author_obj, dict):
            author_name = (
                author_obj.get("displayName")
                or author_obj.get("name")
                or author_obj.get("username")
                or "Anonymous"
            )
        elif isinstance(author_obj, str):
            author_name = author_obj

        owner_reply = None
        reply_obj = item.get("bizUserPublicReply")
        if isinstance(reply_obj, dict):
            owner_reply = reply_obj.get("text")
        elif isinstance(reply_obj, str):
            owner_reply = reply_obj

        reviews.append({
            "author": author_name,
            "rating": rating,
            "date": format_date(item.get("reviewCreatedAt") or item.get("date")),
            "comment": comment_text,
            "owner_reply": owner_reply,
        })
    return reviews


def extract_reviews(items):
    reviews = []

    for item in items:
        data = item.get("latestReviews") or item.get("reviews")

        if data is None:
            data = [item]
        elif not isinstance(data, list):
            data = [data]

        for r in data:
            if not isinstance(r, dict):
                continue

            comment_text = r.get("text")
            if isinstance(comment_text, dict):
                comment_text = comment_text.get("full")

            comment_text = (
                comment_text
                or r.get("comment")
                or r.get("reviewText")
            )

            if not comment_text and not (r.get("stars") or r.get("rating")):
                continue

            raw_rating = r.get("stars") or r.get("rating") or "N/A"
            if isinstance(raw_rating, (int, float)) and raw_rating > 5:
                rating = raw_rating / 10
            else:
                rating = raw_rating

            author_name = "Anonymous"
            user_obj = r.get("user") or r.get("author")
            if isinstance(user_obj, dict):
                author_name = (
                    user_obj.get("name")
                    or user_obj.get("username")
                    or user_obj.get("displayName")
                    or "Anonymous"
                )

            if author_name == "Anonymous":
                author_name = (
                    r.get("author")
                    or r.get("name")
                    or r.get("user_name")
                    or "Anonymous"
                )

            reviews.append({
                "author": author_name,
                "rating": rating,
                "date": format_date(
                    r.get("publishedAtDate")
                    or r.get("date")
                    or r.get("localizedDate")
                ),
                "comment": comment_text,
                "owner_reply": (
                    r.get("responseFromOwnerText")
                    or r.get("ownerReply")
                    or None
                ),
            })

    return reviews


GOOGLE_PLACE_MATCH_LIMIT = 20
_GOOGLE_PLACE_ID_PATTERN = re.compile(r"^ChI[\w-]+$", re.IGNORECASE)


def _looks_like_google_place_id(value: str | None) -> bool:
    if not value or not str(value).strip():
        return False
    return bool(_GOOGLE_PLACE_ID_PATTERN.match(str(value).strip()))


def _resolve_google_place_id(google_place_id: str | None, exact_place: str | None) -> str | None:
    if google_place_id and str(google_place_id).strip():
        return str(google_place_id).strip()
    if exact_place and _looks_like_google_place_id(exact_place):
        return exact_place.strip()
    return None


def _build_google_search_query(
    *,
    business_name: str,
    location: str,
    branch_name: str | None = None,
    exact_place: str | None = None,
    google_place_id: str | None = None,
) -> str:
    parts = [business_name, location]

    if branch_name and str(branch_name).strip():
        parts.append(str(branch_name).strip())
    elif exact_place and not _resolve_google_place_id(google_place_id, exact_place):
        parts.append(str(exact_place).strip())

    return " ".join(part for part in parts if part)


def _extract_place_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"query_place_id=([^&]+)", url, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _find_place_by_google_id(items: list[dict], google_place_id: str) -> dict | None:
    google_place_id = google_place_id.strip()

    for item in items:
        if item.get("placeId") == google_place_id:
            return item

        url_place_id = _extract_place_id_from_url(item.get("url"))
        if url_place_id == google_place_id:
            return item

        item_url = item.get("url") or ""
        if google_place_id in item_url:
            return item

    return None


def _extract_google_business_address(item: dict) -> str:
    address = item.get("address") or item.get("locatedIn")
    if address:
        return address

    parts = [
        item.get("street"),
        item.get("city"),
        item.get("state"),
        item.get("countryCode"),
    ]
    joined = ", ".join(part for part in parts if part)
    return joined or "N/A"


def _extract_google_business_info(item: dict) -> tuple[str, str, str]:
    business_title = item.get("title") or item.get("name") or "N/A"
    business_address = _extract_google_business_address(item)
    business_phone = (
        item.get("phone")
        or item.get("internationalPhoneNumber")
        or "N/A"
    )
    return business_title, business_address, business_phone


def _extract_google_reviews_from_item(item: dict) -> list[dict]:
    google_reviews = []

    for review in item.get("reviews", []):
        owner_reply = (
            review.get("responseFromOwnerText")
            or review.get("ownerResponse")
            or review.get("response")
        )
        google_reviews.append({
            "author": review.get("name", "Anonymous"),
            "rating": review.get("stars", "N/A"),
            "date": format_date(
                review.get("publishedAtDate") or review.get("publishAt")
            ),
            "comment": review.get("text", "[No text]"),
            "owner_reply": owner_reply or None,
        })

    return google_reviews


def _scrape_google_maps(full_search_query: str, google_place_id: str | None = None):
    google_reviews = []
    google_status = "❌ Failed"
    business_title = "N/A"
    business_address = "N/A"
    business_phone = "N/A"

    try:
        logger.info(
            "scrape_reviews: starting Google Maps scrape "
            f"query='{full_search_query}', google_place_id='{google_place_id or 'none'}'"
        )
        actor_input = {
            "searchStringsArray": [full_search_query],
            "maxCrawledPlacesPerSearch": (
                GOOGLE_PLACE_MATCH_LIMIT if google_place_id else 1
            ),
            "maxReviews": 10,
            "language": "en",
            "reviewsSort": "newest",
            "personalDataDeviceType": "desktop",
        }
        google_run = _run_actor_with_retry(
            "compass/crawler-google-places",
            actor_input,
        )
        items = list(client.dataset(get_dataset_id(google_run)).list_items().items)

        if google_place_id:
            matched_item = _find_place_by_google_id(items, google_place_id)
            if not matched_item:
                logger.warning(
                    "scrape_reviews: no Google place matched "
                    f"place_id='{google_place_id}' among {len(items)} result(s)"
                )
                return {
                    "status": (
                        f"❌ Failed: No Google place found for "
                        f"place_id={google_place_id}"
                    ),
                    "business_name": business_title,
                    "business_address": business_address,
                    "business_phone": business_phone,
                    "google_place_id": google_place_id,
                    "reviews": [],
                }

            items = [matched_item]
            logger.info(
                "scrape_reviews: matched Google place "
                f"place_id='{google_place_id}', "
                f"title='{matched_item.get('title') or matched_item.get('name')}'"
            )

        if not items:
            google_status = "❌ Failed: No Google Maps results returned"
            logger.warning("scrape_reviews: Google Maps returned no items")
        else:
            for item in items:
                business_title, business_address, business_phone = (
                    _extract_google_business_info(item)
                )
                google_reviews.extend(_extract_google_reviews_from_item(item))

            google_status = f"✅ Success — {len(google_reviews)} reviews fetched"
            logger.info(f"scrape_reviews: Google Maps {google_status}")
    except Exception as e:
        google_status = f"❌ Failed: {str(e)}"
        logger.exception(f"scrape_reviews: Google Maps scrape failed — {e}")

    return {
        "status": google_status,
        "business_name": business_title,
        "business_address": business_address,
        "business_phone": business_phone,
        "google_place_id": google_place_id,
        "reviews": google_reviews,
    }


def _scrape_yelp(yelp_url: str, full_search_query: str):
    yelp_reviews = []
    yelp_status = "❌ Failed"
    yelp_business_title = "N/A"
    yelp_business_address = "N/A"
    yelp_business_phone = "N/A"

    try:
        logger.info("scrape_reviews: starting Yelp scrape")
        if yelp_url:
            logger.info(f"scrape_reviews: Yelp direct URL scrape url='{yelp_url}'")
            yelp_run = _run_actor_with_retry("api-ninja/yelp-ultimate-scraper", {
                "businessUrl": [yelp_url],
                "reviewsUrl": [yelp_url],
                "categorySearch": False,
                "includeAds": False,
                "numberOfReviews": 40,
                "reviewsSorting": "Newest_first",
                "scrapeAll": False,
                "scrapeAllReviews": False,
                "details": "basic",
                "numberOfResults": 100,
                "ratingReviews": "All_ratings",
                "dishType": "menu",
            })
            items = client.dataset(get_dataset_id(yelp_run)).list_items().items

            if items:
                nap = extract_nap(items)
                yelp_business_title = nap["name"]
                yelp_business_address = nap["address"]
                yelp_business_phone = nap["phone"]
                yelp_reviews = extract_yelp_reviews(items)
                yelp_status = f"✅ Success — {len(yelp_reviews)} reviews"
            else:
                yelp_status = "⚠️ No Yelp data returned"
                logger.warning("scrape_reviews: Yelp direct URL scrape returned no items")

        if not yelp_reviews:
            logger.info("scrape_reviews: Yelp falling Again starting")
            # run = _run_actor_with_retry("tri_angle/yelp-scraper", {
            #     "searchTerms": full_search_query,
            #     "includeReviews": True,
            #     "maxResults": 1,
            #     "proxyConfiguration": {
            #         "useApifyProxy": True,
            #         "apifyProxyGroups": ["RESIDENTIAL"]
            #     }
            # })
            # items = client.dataset(get_dataset_id(run)).list_items().items
            # yelp_reviews = extract_reviews(items)
            # if yelp_reviews:
            #     yelp_status = f"✅ Success — {len(yelp_reviews)} reviews (via search string)"
            
            
            yelp_run = _run_actor_with_retry("api-ninja/yelp-ultimate-scraper", {
                "businessUrl": [yelp_url],
                "reviewsUrl": [yelp_url],
                "categorySearch": False,
                "includeAds": False,
                "numberOfReviews": 40,
                "reviewsSorting": "Newest_first",
                "scrapeAll": False,
                "scrapeAllReviews": False,
                "details": "basic",
                "numberOfResults": 100,
                "ratingReviews": "All_ratings",
                "dishType": "menu",
            })
            items = client.dataset(get_dataset_id(yelp_run)).list_items().items

            if items:
                nap = extract_nap(items)
                yelp_business_title = nap["name"]
                yelp_business_address = nap["address"]
                yelp_business_phone = nap["phone"]
                yelp_reviews = extract_yelp_reviews(items)
                yelp_status = f"✅ Success — {len(yelp_reviews)} reviews"
            else:
                yelp_status = "⚠️ No Yelp data returned"
                logger.warning("scrape_reviews: Yelp direct URL scrape returned no items")

        logger.info(f"scrape_reviews: Yelp {yelp_status}")
    except Exception as e:
        yelp_status = f"❌ Failed: {str(e)}"
        logger.exception(f"scrape_reviews: Yelp scrape failed — {e}")

    return {
        "status": yelp_status,
        "business_name": yelp_business_title,
        "business_address": yelp_business_address,
        "business_phone": yelp_business_phone,
        "reviews": yelp_reviews,
    }


def _scrape_tripadvisor(tripadvisor_url: str | None):
    tripadvisor_reviews = []
    tripadvisor_status = "❌ Failed"
    business_name = "N/A"
    business_address = "N/A"
    business_phone = "N/A"

    if not tripadvisor_url:
        tripadvisor_status = "⏭️ Skipped — no TripAdvisor URL provided"
        logger.info("scrape_reviews: TripAdvisor skipped — no tripadvisor_url provided")
        return {
            "status": tripadvisor_status,
            "business_name": business_name,
            "business_address": business_address,
            "business_phone": business_phone,
            "reviews": tripadvisor_reviews,
        }
    clean_url = re.sub(r'-or\d+-', '-', tripadvisor_url)

    try:
        # --- NAP: crawlerbros/tripadvisor-scraper ---
        logger.info(f"scrape_reviews: TripAdvisor NAP scrape (crawlerbros) url='{clean_url}'")
        detail_run = _run_actor_with_retry("crawlerbros/tripadvisor-scraper", {
            "startUrls": [clean_url],
            "maxItems": 1,
            "placeType": "all",
        })
        detail_items = client.dataset(get_dataset_id(detail_run)).list_items().items
        logger.info(f"scrape_reviews: TripAdvisor NAP raw keys={list(detail_items[0].keys()) if detail_items else 'no items'}")

        if detail_items:
            biz = detail_items[0]
            business_name = (
                biz.get("name")
                or biz.get("title")
                or biz.get("hotelName")
                or "N/A"
            )
            addr = biz.get("address") or biz.get("addressObj") or {}
            if isinstance(addr, dict):
                parts = [
                    addr.get("street1"), addr.get("street2"),
                    addr.get("city"), addr.get("postalcode"),
                    addr.get("country")
                ]
                business_address = ", ".join(p for p in parts if p) or "N/A"
            elif isinstance(addr, str):
                business_address = addr or "N/A"

            business_phone = (
                biz.get("phone")
                or biz.get("telephone")
                or biz.get("phoneNumber")
                or "N/A"
            )


        ta_run = _run_actor_with_retry("maxcopell/tripadvisor-reviews", {
            "startUrls": [{"url": tripadvisor_url}],
            "maxReviews": 10,
        })
        for item in client.dataset(get_dataset_id(ta_run)).list_items().items:
            user_obj = item.get("user") or {}
            author_name = "Anonymous"

            if isinstance(user_obj, dict):
                author_name = user_obj.get("username") or user_obj.get("displayName") or "Anonymous"

            if author_name == "Anonymous":
                author_name = (
                    item.get("username")
                    or item.get("user_username")
                    or item.get("author")
                    or "Anonymous"
                )

            tripadvisor_reviews.append({
                "author": author_name,
                "rating": item.get("rating", "N/A"),
                "date": format_date(item.get("publishedDate") or item.get("date")),
                "comment": item.get("text", "[No text]"),
                "owner_reply": item.get("ownerResponse") or None,
            })
        tripadvisor_status = f"✅ Success — {len(tripadvisor_reviews)} reviews fetched"
        logger.info(f"scrape_reviews: TripAdvisor {tripadvisor_status}")
    except Exception as e:
        tripadvisor_status = f"❌ Failed: {str(e)}"
        logger.exception(f"scrape_reviews: TripAdvisor scrape failed — {e}")

    return {
        "status": tripadvisor_status,
        "business_name": business_name,
        "business_address": business_address,
        "business_phone": business_phone,
        "reviews": tripadvisor_reviews,
    }


def _is_empty_url(url: str | None) -> bool:
    return not url or not str(url).strip()


def _skipped_platform_result(platform: str) -> dict:
    return {
        "status": f"⚠️ Skipped — {platform} link is empty",
        "business_name": "N/A",
        "business_address": "N/A",
        "business_phone": "N/A",
        "reviews": [],
    }


def _failed_platform_result(platform: str, exc: BaseException) -> dict:
    logger.exception(f"scrape_reviews: {platform} scrape failed — {exc}")
    return {
        "status": f"❌ Failed: {str(exc)}",
        "business_name": "N/A",
        "business_address": "N/A",
        "business_phone": "N/A",
        "reviews": [],
    }


def _resolve_platform_result(
    platform: str,
    result: dict | BaseException,
) -> dict:
    if isinstance(result, BaseException):
        return _failed_platform_result(platform, result)
    return result


async def scrape_reviews(payload: ScrapeRequest) -> dict:
    google_place_id = _resolve_google_place_id(
        payload.google_place_id,
        payload.exact_place,
    )
    full_search_query = _build_google_search_query(
        business_name=payload.business_name,
        location=payload.location,
        branch_name=payload.branch_name,
        exact_place=payload.exact_place,
        google_place_id=payload.google_place_id,
    )

    logger.info(
        f"scrape_reviews: request received business='{payload.business_name}', "
        f"business_id='{payload.business_id}', branch_name='{payload.branch_name}', "
        f"location='{payload.location}', exact_place='{payload.exact_place}', "
        f"google_place_id='{google_place_id}', "
        f"yelp_url='{payload.yelp_url}', tripadvisor_url='{payload.tripadvisor_url}'"
    )
    logger.info(f"scrape_reviews: search query='{full_search_query}'")

    yelp_url = normalize_yelp_url(payload.yelp_url)
    if payload.yelp_url and yelp_url and yelp_url != payload.yelp_url.strip():
        logger.info(
            f"scrape_reviews: normalized Yelp URL "
            f"'{payload.yelp_url}' -> '{yelp_url}'"
        )

    tripadvisor_url = normalize_tripadvisor_url(payload.tripadvisor_url)
    if (
        payload.tripadvisor_url
        and tripadvisor_url
        and tripadvisor_url != payload.tripadvisor_url.strip()
    ):
        logger.info(
            f"scrape_reviews: normalized TripAdvisor URL "
            f"'{payload.tripadvisor_url}' -> '{tripadvisor_url}'"
        )

    scrape_tasks: dict[str, asyncio.Task] = {
        "google_maps": asyncio.create_task(
            asyncio.to_thread(
                _scrape_google_maps,
                full_search_query,
                google_place_id,
            )
        ),
    }

    if _is_empty_url(yelp_url):
        logger.info("scrape_reviews: Yelp skipped — yelp_url is empty")
        yelp = _skipped_platform_result("Yelp")
    else:
        scrape_tasks["yelp"] = asyncio.create_task(
            asyncio.to_thread(_scrape_yelp, yelp_url, full_search_query)
        )

    if _is_empty_url(tripadvisor_url):
        logger.info("scrape_reviews: TripAdvisor skipped — tripadvisor_url is empty")
        tripadvisor = _skipped_platform_result("TripAdvisor")
    else:
        scrape_tasks["tripadvisor"] = asyncio.create_task(
            asyncio.to_thread(_scrape_tripadvisor, tripadvisor_url)
        )

    logger.info(
        "scrape_reviews: running platform scrapes in parallel — "
        f"platforms={list(scrape_tasks.keys())}"
    )

    platform_results = await asyncio.gather(
        *scrape_tasks.values(),
        return_exceptions=True,
    )

    resolved_results = {
        platform: _resolve_platform_result(platform, result)
        for platform, result in zip(scrape_tasks.keys(), platform_results)
    }

    google = resolved_results["google_maps"]
    if "yelp" in resolved_results:
        yelp = resolved_results["yelp"]
    if "tripadvisor" in resolved_results:
        tripadvisor = resolved_results["tripadvisor"]

    result = {
        "business": full_search_query,
        "business_id": payload.business_id,
        "branch_name": payload.branch_name,
        "google_place_id": google_place_id,
        "scraped_at": datetime.now().strftime("%d %B %Y %H:%M"),
        "summary": {
            "google_maps": google["status"],
            "yelp": yelp["status"],
            "tripadvisor": tripadvisor["status"],
        },
        "google_maps": {
            "business_name": google["business_name"],
            "business_address": google["business_address"],
            "business_phone": google["business_phone"],
            "google_place_id": google.get("google_place_id"),
            "total_reviews": len(google["reviews"]),
            "reviews": google["reviews"],
        },
        "yelp": {
            "business_name": yelp["business_name"],
            "business_address": yelp["business_address"],
            "business_phone": yelp["business_phone"],
            "total_reviews": len(yelp["reviews"]),
            "reviews": yelp["reviews"],
        },
        "tripadvisor": {
            "business_name": tripadvisor["business_name"],
            "business_address": tripadvisor["business_address"],
            "business_phone": tripadvisor["business_phone"],
            "total_reviews": len(tripadvisor["reviews"]),
            "reviews": tripadvisor["reviews"],
        },
    }

    save_scraped_result(result, payload.business_name, payload.business_id)

    logger.info(
        f"scrape_reviews: completed google={len(google['reviews'])}, "
        f"yelp={len(yelp['reviews'])}, tripadvisor={len(tripadvisor['reviews'])}"
    )
    
    logger.info("scrape_reviews: %s", result)
    return result
