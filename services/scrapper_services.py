from datetime import datetime
from pathlib import Path
import json
import os
import re

from apify_client import ApifyClient

from core.config import settings
from schema.scrapper import ScrapeRequest
from services.logger_services import logger
from services.s3_service import upload_scraped_result_to_s3
from utils.scraped_result_paths import get_scraped_result_path

APIFY_API_TOKEN = settings.Apify_API
client = ApifyClient(APIFY_API_TOKEN)


def save_scraped_result(data: dict, business_name: str) -> None:
    try:
        business_name = business_name.strip()
        # scraping_results/<business_slug>/scraped_result.json
        result_path = get_scraped_result_path(business_name)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = result_path.with_suffix(".json.tmp")

        json_text = json.dumps(data, indent=2, ensure_ascii=False)
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(json_text)

        os.replace(temp_path, result_path)
        logger.info(f"save_scraped_result: saved locally to {result_path}")

        if not upload_scraped_result_to_s3(business_name, result_path):
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


def _scrape_google_maps(full_search_query: str):
    google_reviews = []
    google_status = "❌ Failed"
    business_title = "N/A"
    business_address = "N/A"
    business_phone = "N/A"

    try:
        logger.info("scrape_reviews: starting Google Maps scrape")
        google_run = client.actor("compass/crawler-google-places").call(run_input={
            "searchStringsArray": [full_search_query],
            "maxCrawledPlacesPerSearch": 1,
            "maxReviews": 20,
            "language": "en",
            "reviewsSort": "newest",
            "personalDataDeviceType": "desktop"
        })
        for item in client.dataset(get_dataset_id(google_run)).list_items().items:
            business_title = item.get("title") or item.get("name") or business_title
            business_address = item.get("address") or item.get("locatedIn") or business_address
            business_phone = (
                item.get("phone")
                or item.get("internationalPhoneNumber")
                or business_phone
            )

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
        "reviews": google_reviews,
    }


def _scrape_yelp(payload: ScrapeRequest, full_search_query: str):
    yelp_reviews = []
    yelp_status = "❌ Failed"
    yelp_business_title = "N/A"
    yelp_business_address = "N/A"
    yelp_business_phone = "N/A"

    try:
        logger.info("scrape_reviews: starting Yelp scrape")
        if payload.yelp_url:
            logger.info(f"scrape_reviews: Yelp direct URL scrape url='{payload.yelp_url}'")
            yelp_run = client.actor("api-ninja/yelp-ultimate-scraper").call(run_input={
                "businessUrl": [payload.yelp_url],
                "reviewsUrl": [payload.yelp_url],
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
            logger.info("scrape_reviews: Yelp falling back to search string scrape")
            run = client.actor("triangle/yelp-scraper").call(run_input={
                "searchTerms": full_search_query,
                "includeReviews": True,
                "maxResults": 1,
                "proxyConfiguration": {
                    "useApifyProxy": True,
                    "apifyProxyGroups": ["RESIDENTIAL"]
                }
            })
            items = client.dataset(get_dataset_id(run)).list_items().items
            yelp_reviews = extract_reviews(items)
            if yelp_reviews:
                yelp_status = f"✅ Success — {len(yelp_reviews)} reviews (via search string)"
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
        detail_run = client.actor("crawlerbros/tripadvisor-scraper").call(run_input={
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


        ta_run = client.actor("maxcopell/tripadvisor-reviews").call(run_input={
            "startUrls": [{"url": tripadvisor_url}],
            "maxReviews": 20,
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


def scrape_reviews(payload: ScrapeRequest) -> dict:
    logger.info(
        f"scrape_reviews: request received business='{payload.business_name}', "
        f"location='{payload.location}', exact_place='{payload.exact_place}', "
        f"yelp_url='{payload.yelp_url}', tripadvisor_url='{payload.tripadvisor_url}'"
    )

    full_search_query = f"{payload.business_name} {payload.location} {payload.exact_place}"
    logger.info(f"scrape_reviews: search query='{full_search_query}'")

    google = _scrape_google_maps(full_search_query)
    yelp = _scrape_yelp(payload, full_search_query)
    tripadvisor = _scrape_tripadvisor(payload.tripadvisor_url)

    result = {
        "business": full_search_query,
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

    save_scraped_result(result, payload.business_name)

    logger.info(
        f"scrape_reviews: completed google={len(google['reviews'])}, "
        f"yelp={len(yelp['reviews'])}, tripadvisor={len(tripadvisor['reviews'])}"
    )
    
    logger.info("scrape_reviews: %s", result)
    return result
