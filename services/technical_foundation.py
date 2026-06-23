import httpx
import json
import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup

from core.config import settings
from services.logger_services import logger
from utils.scraped_result_paths import get_scraped_result_path, slugify_folder_name

PAGESPEED_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
STRATEGIES = ["mobile", "desktop"]
MAX_PAGESPEED_SCORE = 4
RAW_MAX_STRATEGY_SCORE = 4  # max raw pts per strategy: LCP(2) + performance(1) + presence(1)
MAX_LLMS_TXT_SCORE = 5
MAX_JSON_LD_SCORE = 5
MAX_NAP_CONSISTENCY_SCORE = 6
MAX_DDI_TECHNICAL_FOUNDATION_SCORE = (
    MAX_PAGESPEED_SCORE
    + MAX_LLMS_TXT_SCORE
    + MAX_JSON_LD_SCORE
    + MAX_NAP_CONSISTENCY_SCORE
)
NAP_PLATFORMS = ("google_maps", "yelp", "tripadvisor")
JSON_LD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def _log_section(title: str) -> None:
    logger.info(f"technical_foundation: {'=' * 60}")
    logger.info(f"technical_foundation: {title}")
    logger.info(f"technical_foundation: {'=' * 60}")


def _parse_lcp_seconds(lcp_value: str | None) -> float | None:
    if not lcp_value:
        return None
    try:
        return float(lcp_value.strip().split()[0])
    except (ValueError, IndexError):
        logger.warning(f"technical_foundation: could not parse LCP value '{lcp_value}'")
        return None


def _score_lcp(lcp_seconds: float | None) -> int:
    if lcp_seconds is None:
        return 0
    if lcp_seconds < 2.5:
        return 2
    if lcp_seconds <= 4.0:
        return 1
    return 0


def _score_performance(performance_score: float | None) -> int:
    if performance_score is None:
        return 0
    if performance_score >= 90:
        return 1
    else:
        return 0


def _calculate_single_strategy_score(strategy_result: dict | None) -> dict:
    if not strategy_result:
        return {
            "score": 0,
            "lcp_seconds": None,
            "lcp_pts": 0,
            "performance_pts": 0,
            "strategy_pts": 0,
        }

    lcp_seconds = _parse_lcp_seconds(strategy_result.get("largest_contentful_paint"))
    performance_score = strategy_result.get("performance_score")
    lcp_pts = _score_lcp(lcp_seconds)
    performance_pts = _score_performance(performance_score)
    strategy_pts = 1
    raw_score = lcp_pts + performance_pts + strategy_pts
    score = round((raw_score / RAW_MAX_STRATEGY_SCORE) * MAX_PAGESPEED_SCORE, 2)

    return {
        "score": score,
        "lcp_seconds": lcp_seconds,
        "lcp_pts": lcp_pts,
        "performance_pts": performance_pts,
        "strategy_pts": strategy_pts,
        "performance_score": performance_score,
    }


def calculate_pagespeed_score(pagespeed_data: dict) -> dict:
    results = pagespeed_data.get("results", [])
    mobile_result = next(
        (item for item in results if item.get("strategy") == "mobile"),
        None,
    )
    desktop_result = next(
        (item for item in results if item.get("strategy") == "desktop"),
        None,
    )

    mobile_score = _calculate_single_strategy_score(mobile_result)
    desktop_score = _calculate_single_strategy_score(desktop_result)

    combined_score = round(
        (mobile_score["score"] + desktop_score["score"]) / len(STRATEGIES), 2
    )
    avg_lcp_pts = round(
        (mobile_score["lcp_pts"] + desktop_score["lcp_pts"]) / len(STRATEGIES), 2
    )
    avg_performance_pts = round(
        (mobile_score["performance_pts"] + desktop_score["performance_pts"])
        / len(STRATEGIES),
        2,
    )

    score_result = {
        "score": combined_score,
        "max_score": MAX_PAGESPEED_SCORE,
        "lcp_pts": avg_lcp_pts,
        "performance_pts": avg_performance_pts,
        "mobile": mobile_score,
        "desktop": desktop_score,
    }

    logger.info(
        "technical_foundation: pagespeed score calculated — "
        f"mobile={mobile_score['score']}/{MAX_PAGESPEED_SCORE}, "
        f"desktop={desktop_score['score']}/{MAX_PAGESPEED_SCORE}"
    )
    logger.info(
        "technical_foundation: mobile breakdown — "
        f"lcp_seconds={mobile_score['lcp_seconds']}, lcp_pts={mobile_score['lcp_pts']}, "
        f"performance_pts={mobile_score['performance_pts']}, "
        f"strategy_pts={mobile_score['strategy_pts']}"
    )
    logger.info(
        "technical_foundation: desktop breakdown — "
        f"lcp_seconds={desktop_score['lcp_seconds']}, lcp_pts={desktop_score['lcp_pts']}, "
        f"performance_pts={desktop_score['performance_pts']}, "
        f"strategy_pts={desktop_score['strategy_pts']}"
    )
    print(
        f"\nPageSpeed Scores:\n"
        f"  Combined: {combined_score}/{MAX_PAGESPEED_SCORE}\n"
        f"  Mobile  : {mobile_score['score']}/{MAX_PAGESPEED_SCORE} "
        f"(LCP={mobile_score['lcp_pts']}, Performance={mobile_score['performance_pts']}, "
        f"Strategy={mobile_score['strategy_pts']})\n"
        f"  Desktop : {desktop_score['score']}/{MAX_PAGESPEED_SCORE} "
        f"(LCP={desktop_score['lcp_pts']}, Performance={desktop_score['performance_pts']}, "
        f"Strategy={desktop_score['strategy_pts']})\n"
    )

    return score_result


def _score(category: dict) -> float:
    raw = category.get("score")
    return round((raw or 0) * 100, 2)


def _extract_strategy_result(website_url: str, strategy: str, data: dict) -> dict:
    lighthouse = data.get("lighthouseResult", {})
    categories = lighthouse.get("categories", {})
    audits = lighthouse.get("audits", {})

    return {
        "website": website_url,
        "strategy": strategy,
        "performance_score": _score(categories.get("performance", {})),
        "seo_score": _score(categories.get("seo", {})),
        "accessibility_score": _score(categories.get("accessibility", {})),
        "best_practices_score": _score(categories.get("best-practices", {})),
        "first_contentful_paint": audits.get("first-contentful-paint", {}).get("displayValue"),
        "largest_contentful_paint": audits.get("largest-contentful-paint", {}).get("displayValue"),
        "speed_index": audits.get("speed-index", {}).get("displayValue"),
        "total_blocking_time": audits.get("total-blocking-time", {}).get("displayValue"),
        "cumulative_layout_shift": audits.get("cumulative-layout-shift", {}).get("displayValue"),
    }


def _log_strategy_result(strategy_result: dict) -> None:
    strategy = strategy_result["strategy"]
    logger.info(f"technical_foundation: [{strategy}] Performance Score      = {strategy_result['performance_score']}")
    logger.info(f"technical_foundation: [{strategy}] SEO Score              = {strategy_result['seo_score']}")
    logger.info(f"technical_foundation: [{strategy}] Accessibility Score    = {strategy_result['accessibility_score']}")
    logger.info(f"technical_foundation: [{strategy}] Best Practices Score   = {strategy_result['best_practices_score']}")
    logger.info(f"technical_foundation: [{strategy}] First Contentful Paint = {strategy_result['first_contentful_paint']}")
    logger.info(f"technical_foundation: [{strategy}] Largest Contentful Paint = {strategy_result['largest_contentful_paint']}")
    logger.info(f"technical_foundation: [{strategy}] Speed Index            = {strategy_result['speed_index']}")
    logger.info(f"technical_foundation: [{strategy}] Total Blocking Time    = {strategy_result['total_blocking_time']}")
    logger.info(f"technical_foundation: [{strategy}] Cumulative Layout Shift = {strategy_result['cumulative_layout_shift']}")

    print(f"\n===== PAGESPEED RESULTS — {strategy.upper()} =====\n")
    for key, value in strategy_result.items():
        if key == "website":
            continue
        print(f"{key.replace('_', ' ').title()}: {value}")


def _build_llms_txt_urls(website_url: str) -> tuple[str, str]:
    parsed_url = urlparse(website_url)
    if not parsed_url.scheme or not parsed_url.netloc:
        raise ValueError(f"Invalid URL format: {website_url}")
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    llms_txt_url = f"{base_url}/llms.txt"
    return base_url, llms_txt_url


def check_llms_txt(website_url: str) -> dict:
    _log_section("Technical Foundation — llms.txt check started")
    logger.info(f"technical_foundation: checking llms.txt for website_url='{website_url}'")

    base_url, llms_txt_url = _build_llms_txt_urls(website_url)

    score = 0
    status_code = None
    message = ""

    try:
        response = httpx.get(llms_txt_url, timeout=10)
        status_code = response.status_code

        if response.status_code == 200:
            content = response.text.strip()
            if content:
                score = 5
                message = "llms.txt found and contains content."
            else:
                message = "llms.txt is empty."
        elif response.status_code == 404:
            message = "llms.txt not found."
        else:
            message = f"Unexpected HTTP status: {response.status_code}"

    except httpx.TimeoutException:
        message = "Request timed out."
    except httpx.RequestError as e:
        message = f"Request failed: {str(e)}"

    result = {
        "website_url": base_url,
        "llms_txt_url": llms_txt_url,
        "http_status": status_code,
        "score": score,
        "max_score": MAX_LLMS_TXT_SCORE,
        "message": message,
    }

    logger.info(
        "technical_foundation: llms.txt check completed — "
        f"url='{llms_txt_url}', http_status={status_code}, "
        f"score={score}/{MAX_LLMS_TXT_SCORE}, message='{message}'"
    )
    return result


def check_json_ld(website_url: str) -> dict:
    _log_section("Technical Foundation — JSON-LD check started")
    logger.info(f"technical_foundation: checking JSON-LD for website_url='{website_url}'")

    score = 0
    found = False
    status_code = None
    message = ""

    try:
        response = httpx.get(
            website_url,
            headers=JSON_LD_HEADERS,
            timeout=10,
            follow_redirects=True,
        )
        status_code = response.status_code

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            json_ld_tags = soup.find_all("script", type="application/ld+json")

            if json_ld_tags:
                found = True
                score = 5
                message = "JSON-LD schema found."
            else:
                message = "JSON-LD schema not found."
        else:
            message = f"HTTP error: {response.status_code}"

    except httpx.TimeoutException:
        message = "Request timed out."
    except httpx.RequestError as e:
        message = f"Connection error: {str(e)}"

    result = {
        "website_url": website_url,
        "found": found,
        "http_status": status_code,
        "score": score,
        "max_score": MAX_JSON_LD_SCORE,
        "message": message,
    }

    logger.info(
        "technical_foundation: JSON-LD check completed — "
        f"url='{website_url}', found={found}, http_status={status_code}, "
        f"score={score}/{MAX_JSON_LD_SCORE}, message='{message}'"
    )
    return result


def _normalize_business_name(name: str) -> str:
    if not name or str(name).strip().upper() == "N/A":
        return ""
    cleaned = re.sub(r"[^\w\s]", "", str(name).lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _normalize_phone(phone: str) -> str:
    if not phone or str(phone).strip().upper() == "N/A":
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if digits.startswith("44") and len(digits) > 10:
        digits = digits[2:]
    if digits.startswith("0"):
        digits = digits[1:]
    return digits


def _normalize_address(address: str) -> str:
    if not address or str(address).strip().upper() == "N/A":
        return ""
    text = str(address).lower()
    text = re.sub(r"\broad\b", "rd", text)
    text = re.sub(r"\bstreet\b", "st", text)
    text = re.sub(r"\b(united kingdom|england|great britain|uk|gb)\b", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(
        r"([a-z]{1,2}\d[a-z\d]?)\s+(\d[a-z]{2})",
        r"\1\2",
        text,
    )
    return text


def _parse_address_parts(address: str) -> tuple[str, str, tuple[str, ...]]:
    if not address or str(address).strip().upper() == "N/A":
        return "", "", ()

    text = str(address).lower()
    text = re.sub(r"\broad\b", "rd", text)
    text = re.sub(r"\bstreet\b", "st", text)
    text = re.sub(r"\b(united kingdom|england|great britain|uk|gb)\b", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    postcode_match = re.search(r"([a-z]{1,2}\d[a-z\d]?)\s*(\d[a-z]{2})", text)
    postcode = postcode_match.group(1) + postcode_match.group(2) if postcode_match else ""
    if postcode:
        text = re.sub(r"([a-z]{1,2}\d[a-z\d]?)\s*(\d[a-z]{2})", " ", text)

    for city in (
        "london", "manchester", "birmingham", "edinburgh", "glasgow",
        "liverpool", "bristol", "leeds", "sheffield", "cardiff", "belfast",
    ):
        text = re.sub(rf"\b{city}\b", " ", text)

    tokens = [token for token in text.split() if token]
    if not tokens:
        return "", postcode, ()

    street_number = tokens[0] if tokens[0].isdigit() else ""
    street_tokens = tuple(tokens[1:] if street_number else tokens)
    return street_number, postcode, street_tokens


def _addresses_match(addresses: list[str]) -> bool:
    parsed = [_parse_address_parts(address) for address in addresses]
    if any(not number or not postcode or not tokens for number, postcode, tokens in parsed):
        return False

    numbers = [parts[0] for parts in parsed]
    postcodes = [parts[1] for parts in parsed]
    if not _values_match(numbers) or not _values_match(postcodes):
        return False

    token_sets = [set(parts[2]) for parts in parsed]
    base_tokens = min(token_sets, key=len)
    return all(base_tokens.issubset(token_set) for token_set in token_sets)


def _extract_platform_nap(scraped_data: dict, platform_key: str) -> dict:
    platform_data = scraped_data.get(platform_key, {})
    return {
        "name": platform_data.get("business_name", "N/A"),
        "address": platform_data.get("business_address", "N/A"),
        "phone": platform_data.get("business_phone", "N/A"),
    }


def _values_match(values: list[str]) -> bool:
    if not values or any(not value for value in values):
        return False
    return len(set(values)) == 1


def check_nap_consistency(business_name: str) -> dict:
    _log_section("Technical Foundation — NAP consistency check started")

    business_name = business_name.strip()
    folder_slug = slugify_folder_name(business_name)
    scraped_result_path = get_scraped_result_path(business_name)

    logger.info(
        f"technical_foundation: business_name='{business_name}', folder_slug='{folder_slug}'"
    )
    logger.info(f"technical_foundation: looking for scraped data at {scraped_result_path}")

    if not scraped_result_path.exists():
        message = (
            f"No scraped result is present against this business name: '{business_name}'."
        )
        logger.warning(f"technical_foundation: {message}")
        result = {
            "score": 0,
            "max_score": MAX_NAP_CONSISTENCY_SCORE,
            "consistent": False,
            "message": message,
            "platforms": {},
            "normalized": {},
            "matches": {
                "name": False,
                "address": False,
                "phone": False,
            },
        }
        print(
            f"\nNAP Consistency Score: 0/{MAX_NAP_CONSISTENCY_SCORE}\n  {message}\n"
        )
        return result

    with open(scraped_result_path, encoding="utf-8") as f:
        scraped_data = json.load(f)

    platform_nap = {
        platform: _extract_platform_nap(scraped_data, platform)
        for platform in NAP_PLATFORMS
    }
    normalized = {
        platform: {
            "name": _normalize_business_name(nap["name"]),
            "address": _normalize_address(nap["address"]),
            "phone": _normalize_phone(nap["phone"]),
        }
        for platform, nap in platform_nap.items()
    }

    name_match = _values_match([data["name"] for data in normalized.values()])
    address_match = _addresses_match([nap["address"] for nap in platform_nap.values()])
    phone_match = _values_match([data["phone"] for data in normalized.values()])
    consistent = name_match and address_match and phone_match
    score = MAX_NAP_CONSISTENCY_SCORE if consistent else 0

    if consistent:
        message = "NAP data is consistent across Google Maps, Yelp, and TripAdvisor."
    else:
        mismatches = []
        if not name_match:
            mismatches.append("name")
        if not address_match:
            mismatches.append("address")
        if not phone_match:
            mismatches.append("phone")
        message = f"NAP mismatch detected: {', '.join(mismatches)}."

    result = {
        "score": score,
        "max_score": MAX_NAP_CONSISTENCY_SCORE,
        "consistent": consistent,
        "message": message,
        "platforms": platform_nap,
        "normalized": normalized,
        "matches": {
            "name": name_match,
            "address": address_match,
            "phone": phone_match,
        },
    }

    logger.info(
        "technical_foundation: NAP consistency check completed — "
        f"score={score}/{MAX_NAP_CONSISTENCY_SCORE}, consistent={consistent}, "
        f"name_match={name_match}, address_match={address_match}, phone_match={phone_match}"
    )

    print(f"\nNAP Consistency Score: {score}/{MAX_NAP_CONSISTENCY_SCORE}\n")
    print(f"  Consistent : {consistent}")
    print(f"  Message    : {message}\n")
    for platform in NAP_PLATFORMS:
        raw = platform_nap[platform]
        norm = normalized[platform]
        print(f"  [{platform}]")
        print(f"    Name (raw)       : {raw['name']}")
        print(f"    Name (normalized): {norm['name'] or 'N/A'}")
        print(f"    Address (raw)       : {raw['address']}")
        print(f"    Address (normalized): {norm['address'] or 'N/A'}")
        print(f"    Phone (raw)       : {raw['phone']}")
        print(f"    Phone (normalized): {norm['phone'] or 'N/A'}")
    print(
        f"\n  Matches — name: {name_match}, address: {address_match}, phone: {phone_match}\n"
    )

    return result


def calculate_ddi_technical_foundation_score(
    pagespeed_score: dict | None,
    llms_txt_result: dict,
    json_ld_result: dict,
    nap_consistency_result: dict,
) -> float:
    pagespeed_points = float((pagespeed_score or {}).get("score") or 0)
    llms_points = float(llms_txt_result.get("score") or 0)
    json_ld_points = float(json_ld_result.get("score") or 0)
    nap_points = float(nap_consistency_result.get("score") or 0)

    total = round(pagespeed_points + llms_points + json_ld_points + nap_points, 2)

    logger.info(
        "technical_foundation: DDI score calculated — "
        f"pagespeed={pagespeed_points}/{MAX_PAGESPEED_SCORE}, "
        f"llms_txt={llms_points}/{MAX_LLMS_TXT_SCORE}, "
        f"json_ld={json_ld_points}/{MAX_JSON_LD_SCORE}, "
        f"nap={nap_points}/{MAX_NAP_CONSISTENCY_SCORE}, "
        f"total={total}/{MAX_DDI_TECHNICAL_FOUNDATION_SCORE}"
    )

    print("pagespeed result          =", pagespeed_points)
    print("llms_txt result           =", llms_points)
    print("json_ld result            =", json_ld_points)
    print("nap_consistency result    =", nap_points)
    print(f'    "DDI_technical_foundation_Result": {total},')
    print(f'    "max_technical_foundation_Score": {MAX_DDI_TECHNICAL_FOUNDATION_SCORE}\n')

    return total


def check_technical_foundation(website_url: str, business_name: str) -> dict:
    llms_txt_result = check_llms_txt(website_url)
    json_ld_result = check_json_ld(website_url)
    nap_consistency_result = check_nap_consistency(business_name)

    _log_section("Technical Foundation — PageSpeed analysis started")
    logger.info(f"technical_foundation: website_url='{website_url}'")
    logger.info(f"technical_foundation: strategies={STRATEGIES}")
    print(f"Website URL : {website_url}")
    print(f"Strategies  : {', '.join(STRATEGIES)}\n")

    results = []

    with httpx.Client(timeout=120.0) as client:
        for index, strategy in enumerate(STRATEGIES, start=1):
            _log_section(f"Step {index} — PageSpeed API call ({strategy})")
            params = {
                "url": website_url,
                "strategy": strategy,
                "key": settings.Pagespeed_API,
            }

            logger.info(
                f"technical_foundation: calling PageSpeed API — "
                f"endpoint={PAGESPEED_ENDPOINT}, strategy={strategy}"
            )
            print(f"Calling PageSpeed API for: {strategy}")

            response = client.get(PAGESPEED_ENDPOINT, params=params)

            logger.info(
                f"technical_foundation: API response — "
                f"strategy={strategy}, status_code={response.status_code}"
            )
            print(f"Status Code: {response.status_code}")

            if response.status_code != 200:
                logger.error(
                    f"technical_foundation: API error — strategy={strategy}, "
                    f"status={response.status_code}, body={response.text}"
                )
                print(f"\nError:\n{response.text}\n")
                ddi_technical_foundation_result = calculate_ddi_technical_foundation_score(
                    None,
                    llms_txt_result,
                    json_ld_result,
                    nap_consistency_result,
                )
                return {
                    "status": "error",
                    "message": f"PageSpeed API failed for {strategy}",
                    "website": website_url,
                    "details": response.text,
                    "llms_txt": llms_txt_result,
                    "json_ld": json_ld_result,
                    "nap_consistency": nap_consistency_result,
                    "DDI_technical_foundation_Result": ddi_technical_foundation_result,
                    "max_technical_foundation_Score": MAX_DDI_TECHNICAL_FOUNDATION_SCORE,
                }

            strategy_result = _extract_strategy_result(
                website_url, strategy, response.json()
            )
            results.append(strategy_result)
            _log_strategy_result(strategy_result)

    _log_section("Technical Foundation — Final Summary")
    for strategy_result in results:
        logger.info(
            f"technical_foundation: summary [{strategy_result['strategy']}] — "
            f"performance={strategy_result['performance_score']}, "
            f"seo={strategy_result['seo_score']}, "
            f"accessibility={strategy_result['accessibility_score']}, "
            f"best_practices={strategy_result['best_practices_score']}"
        )

    _log_section("Technical Foundation — PageSpeed Score")
    pagespeed_score = calculate_pagespeed_score({"results": results})
    logger.info(
        f"technical_foundation: DDI PageSpeed score = "
        f"{pagespeed_score['score']}/{pagespeed_score['max_score']}"
    )

    _log_section("Technical Foundation — DDI Final Summary")
    ddi_technical_foundation_result = calculate_ddi_technical_foundation_score(
        pagespeed_score,
        llms_txt_result,
        json_ld_result,
        nap_consistency_result,
    )

    logger.info(
        f"technical_foundation: analysis completed successfully for url='{website_url}'"
    )
    logger.info(
        f'technical_foundation: "DDI_technical_foundation_Result": '
        f"{ddi_technical_foundation_result}"
    )
    logger.info(
        f'technical_foundation: "max_technical_foundation_Score": '
        f"{MAX_DDI_TECHNICAL_FOUNDATION_SCORE}"
    )
    print(f"\nTechnical foundation analysis completed for: {website_url}\n")

    return {
        "status": "success",
        "website": website_url,
        "results": results,
        "pagespeed_score": pagespeed_score,
        "llms_txt": llms_txt_result,
        "json_ld": json_ld_result,
        "nap_consistency": nap_consistency_result,
        "DDI_technical_foundation_Result": ddi_technical_foundation_result,
        "max_technical_foundation_Score": MAX_DDI_TECHNICAL_FOUNDATION_SCORE,
    }
