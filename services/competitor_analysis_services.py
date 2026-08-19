from concurrent.futures import ThreadPoolExecutor, as_completed

from services.logger_services import logger
from services.s3_service import fetch_ddi_score_by_business_id

MAX_COMPETITOR_WORKERS = 8

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


def _fetch_one(business_id: str) -> tuple[str, dict | None]:
    result = fetch_ddi_score_by_business_id(business_id)
    return business_id, result


def _extract_score(section: dict | None, *keys: str, default_max: float) -> dict:
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


def _summarize(ddi_result: dict) -> dict:
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
            reputation, "review_velocity", default_max=DEFAULT_MAX_SCORES["review_velocity"]
        ),
        "star_rating_decay": _extract_score(
            reputation, "star_rating_decay", default_max=DEFAULT_MAX_SCORES["star_rating_decay"]
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
            technical, "nap_consistency", default_max=DEFAULT_MAX_SCORES["nap_consistency"]
        ),
    }


def get_competitor_analysis(
    business_id: str,
    competitor_ids: list[str],
) -> dict:
    all_ids = [business_id] + competitor_ids
    worker_count = min(MAX_COMPETITOR_WORKERS, len(all_ids))

    logger.info(
        f"competitor_analysis: fetching DDI scores — "
        f"business_id='{business_id}', competitors={competitor_ids}"
    )

    fetched: dict[str, dict | None] = {}
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(_fetch_one, bid): bid for bid in all_ids}
        for future in as_completed(futures):
            bid, data = future.result()
            fetched[bid] = data

    def _build_entry(bid: str) -> dict:
        data = fetched.get(bid)
        if data is None:
            return {
                "status": "not_found",
                "message": f"No DDI score result found for business_id='{bid}'",
            }
        return _summarize(data)

    competitors_result = {cid: _build_entry(cid) for cid in competitor_ids}

    logger.info(
        f"competitor_analysis: completed — "
        f"business_id='{business_id}', "
        f"competitors_found={sum(1 for v in competitors_result.values() if v.get('status') != 'not_found')}"
    )

    return {
        business_id: _build_entry(business_id),
        "competitors": competitors_result,
    }
