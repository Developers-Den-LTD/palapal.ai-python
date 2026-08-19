import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from schema.ddi_batch_schema import DDIBatchBusinessItem
from services.logger_services import logger
from services.s3_service import (
    ddi_score_exists_in_s3,
    download_ddi_score_result_from_s3,
    get_ddi_score_s3_key,
)
from utils.scraped_result_paths import build_scrape_storage_slug, get_ddi_score_result_path

MAX_BATCH_SIZE = 50
MAX_WORKERS = 8

DEFAULT_MAX_SCORES = {
    "citation_score": 15,
    "exposure_fairness": 10,
    "sentiment_analysis": 15,
    "review_velocity": 15,
    "star_rating_decay": 15,
    "response_rate": 10,
    "pagespeed": 4,
    "llms_txt": 5,
    "json_ld": 5,
    "nap_consistency": 6,
}


def _load_local_ddi_score(local_path) -> dict:
    with open(local_path, "r", encoding="utf-8") as file:
        return json.load(file)


def _extract_score(section: dict | None, *keys: str, default_max: float | int) -> dict:
    data = section or {}
    for key in keys:
        if not isinstance(data, dict):
            data = {}
            break
        data = data.get(key) or {}

    if not isinstance(data, dict):
        data = {}

    score = data.get("score", 0)
    max_score = data.get("max_score", default_max)

    return {
        "score": float(score) if score is not None else 0.0,
        "max_score": float(max_score) if max_score is not None else float(default_max),
    }


def _summarize_ddi_result(ddi_result: dict) -> dict:
    ai = ddi_result.get("ai_visibility") or {}
    reputation = ddi_result.get("reputation") or {}
    technical = ddi_result.get("technical_foundation") or {}

    return {
        "citation_score": _extract_score(
            ai, "citation_score", default_max=DEFAULT_MAX_SCORES["citation_score"]
        ),
        "exposure_fairness": _extract_score(
            ai, "exposure_fairness", default_max=DEFAULT_MAX_SCORES["exposure_fairness"]
        ),
        "sentiment_analysis": _extract_score(
            ai, "sentiment_analysis", default_max=DEFAULT_MAX_SCORES["sentiment_analysis"]
        ),
        "review_velocity": _extract_score(
            reputation,
            "review_velocity",
            default_max=DEFAULT_MAX_SCORES["review_velocity"],
        ),
        "star_rating_decay": _extract_score(
            reputation,
            "star_rating_decay",
            default_max=DEFAULT_MAX_SCORES["star_rating_decay"],
        ),
        "response_rate": _extract_score(
            reputation, "response_rate", default_max=DEFAULT_MAX_SCORES["response_rate"]
        ),
        "pagespeed": _extract_score(
            technical, "pagespeed_score", default_max=DEFAULT_MAX_SCORES["pagespeed"]
        ),
        "llms_txt": _extract_score(
            technical, "llms_txt", default_max=DEFAULT_MAX_SCORES["llms_txt"]
        ),
        "json_ld": _extract_score(
            technical, "json_ld", default_max=DEFAULT_MAX_SCORES["json_ld"]
        ),
        "nap_consistency": _extract_score(
            technical,
            "nap_consistency",
            default_max=DEFAULT_MAX_SCORES["nap_consistency"],
        ),
    }


def _error_result(message: str) -> dict:
    return {
        "status": "error",
        "message": message,
    }


def _fetch_one_business(item: DDIBatchBusinessItem) -> tuple[str, dict]:
    business_name = item.business_name.strip()
    business_id = item.business_id
    storage_slug = build_scrape_storage_slug(business_name, business_id)
    local_path = get_ddi_score_result_path(business_name, business_id)
    s3_key = get_ddi_score_s3_key(business_name, business_id)

    logger.info(
        "ddi_batch: fetching — "
        f"business='{business_name}', business_id='{business_id}', "
        f"slug='{storage_slug}', s3_key='{s3_key}'"
    )

    try:
        if local_path.exists():
            data = _load_local_ddi_score(local_path)
            logger.info(
                f"ddi_batch: loaded from local cache — slug='{storage_slug}', path='{local_path}'"
            )
            return storage_slug, _summarize_ddi_result(data)

        if not ddi_score_exists_in_s3(business_name, business_id):
            message = (
                f"No DDI score result found for '{business_name}'"
                f"{f' with business_id={business_id}' if business_id is not None else ''}."
            )
            logger.warning(f"ddi_batch: {message}")
            return storage_slug, _error_result(message)

        if not download_ddi_score_result_from_s3(
            business_name,
            local_path,
            business_id,
        ):
            message = (
                f"Failed to download DDI score result for '{business_name}'"
                f"{f' with business_id={business_id}' if business_id is not None else ''} from S3."
            )
            logger.error(f"ddi_batch: {message}")
            return storage_slug, _error_result(message)

        data = _load_local_ddi_score(local_path)
        logger.info(
            f"ddi_batch: downloaded from S3 — slug='{storage_slug}', path='{local_path}'"
        )
        return storage_slug, _summarize_ddi_result(data)
    except Exception as exc:
        logger.exception(
            f"ddi_batch: unexpected error for slug='{storage_slug}' — {exc}"
        )
        return storage_slug, _error_result(str(exc))


def get_ddi_scores_for_businesses(items: list[DDIBatchBusinessItem]) -> dict:
    fetched: dict[str, dict] = {}
    worker_count = min(MAX_WORKERS, len(items)) or 1

    logger.info(f"ddi_batch: starting fetch for {len(items)} businesses")

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(_fetch_one_business, item) for item in items]
        for future in as_completed(futures):
            storage_slug, payload = future.result()
            fetched[storage_slug] = payload

    results: dict[str, dict] = {}
    for item in items:
        storage_slug = build_scrape_storage_slug(
            item.business_name.strip(),
            item.business_id,
        )
        if storage_slug in fetched:
            results[storage_slug] = fetched[storage_slug]

    logger.info(f"ddi_batch: completed fetch for {len(results)} businesses")
    return results
