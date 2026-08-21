import json
import re
from collections import Counter

from services.logger_services import logger
from services.s3_service import (
    ddi_score_exists_in_s3,
    download_ddi_score_result_from_s3,
    get_ddi_score_s3_key,
)
from utils.scraped_result_paths import build_scrape_storage_slug, get_ddi_score_result_path

TOP_COMPETITORS_LIMIT = 5


def _load_local_ddi_score(local_path) -> dict:
    with open(local_path, "r", encoding="utf-8") as file:
        return json.load(file)


def _competitor_key(name: str) -> str:
    """Normalize a business name so slight spelling variants map to one key."""
    text = str(name or "").lower().strip()
    for ch in ("'", "'", "`", "´"):
        text = text.replace(ch, "")
    if text.startswith("the "):
        text = text[4:]
    return re.sub(r"[^a-z0-9]", "", text)


def _is_same_business(left: str, right: str) -> bool:
    left_key = _competitor_key(left)
    right_key = _competitor_key(right)
    if not left_key or not right_key:
        return False
    return left_key == right_key


def _collect_mentioned_names(ddi_result: dict) -> list[str]:
    ai = ddi_result.get("ai_visibility") or {}
    if not isinstance(ai, dict) or ai.get("status") == "error":
        return []

    answers = ai.get("answers") or {}
    if not isinstance(answers, dict):
        return []

    names: list[str] = []
    for provider_result in answers.values():
        if not isinstance(provider_result, dict):
            continue
        for item in provider_result.get("answers") or []:
            if not isinstance(item, dict):
                continue
            businesses = item.get("businesses") or []
            if not isinstance(businesses, list):
                continue
            for name in businesses:
                clean = str(name).strip()
                if clean:
                    names.append(clean)
    return names


def _rank_top_competitors(
    mentioned_names: list[str],
    primary_business_name: str,
    limit: int = TOP_COMPETITORS_LIMIT,
) -> list[str]:
    """
    Group name variants, exclude the primary business, and return the top N
    display names ordered by mention frequency (most mentioned first).
    """
    # key -> Counter of raw spellings + total mention count
    clusters: dict[str, Counter] = {}
    mention_totals: Counter = Counter()

    for name in mentioned_names:
        if _is_same_business(primary_business_name, name):
            continue
        key = _competitor_key(name)
        if not key:
            continue
        if key not in clusters:
            clusters[key] = Counter()
        clusters[key][name] += 1
        mention_totals[key] += 1

    ranked_keys = sorted(
        mention_totals.keys(),
        key=lambda key: (-mention_totals[key], clusters[key].most_common(1)[0][0].lower()),
    )

    top_names: list[str] = []
    for key in ranked_keys[:limit]:
        # Prefer the most frequently used spelling as the display name.
        display_name = clusters[key].most_common(1)[0][0]
        top_names.append(display_name)
    return top_names


def get_top_competitor_names(
    business_name: str,
    business_id: str | int,
) -> dict:
    """
    Load DDI score (local first, then S3), extract competitor names mentioned
    across all AI providers, and return the top 5 by mention frequency.
    """
    business_name = business_name.strip()
    storage_slug = build_scrape_storage_slug(business_name, business_id)
    local_path = get_ddi_score_result_path(business_name, business_id)
    s3_key = get_ddi_score_s3_key(business_name, business_id)

    logger.info(
        f"competitor_names: request received business='{business_name}', "
        f"business_id='{business_id}', storage_slug='{storage_slug}', "
        f"local_path='{local_path}', s3_key='{s3_key}'"
    )

    source = "local"
    if local_path.exists():
        logger.info(
            f"competitor_names: loaded from local cache — path='{local_path}'"
        )
        ddi_result = _load_local_ddi_score(local_path)
    else:
        logger.info(
            f"competitor_names: not found locally, checking S3 — "
            f"business='{business_name}', business_id='{business_id}'"
        )
        if not ddi_score_exists_in_s3(business_name, business_id):
            logger.warning(
                f"competitor_names: no DDI score found locally or in S3 for "
                f"'{business_name}' (slug='{storage_slug}')"
            )
            return {
                "status": "error",
                "message": (
                    f"No DDI score result found for '{business_name}' "
                    f"with business_id={business_id}."
                ),
                "business_name": business_name,
                "business_id": business_id,
                "competitors": [],
            }

        if not download_ddi_score_result_from_s3(
            business_name,
            local_path,
            business_id,
        ):
            logger.error(
                f"competitor_names: failed to download DDI score from S3 for "
                f"'{business_name}', business_id='{business_id}'"
            )
            return {
                "status": "error",
                "message": (
                    f"Failed to download DDI score result for '{business_name}' "
                    f"with business_id={business_id} from S3."
                ),
                "business_name": business_name,
                "business_id": business_id,
                "competitors": [],
            }

        source = "s3"
        ddi_result = _load_local_ddi_score(local_path)
        logger.info(
            f"competitor_names: downloaded from S3 and cached locally — "
            f"path='{local_path}'"
        )

    mentioned_names = _collect_mentioned_names(ddi_result)
    competitors = _rank_top_competitors(mentioned_names, business_name)

    logger.info(
        f"competitor_names: completed — business='{business_name}', "
        f"business_id='{business_id}', source='{source}', "
        f"mentions_scanned={len(mentioned_names)}, "
        f"top_competitors={competitors}"
    )

    return {
        "status": "success",
        "business_name": business_name,
        "business_id": business_id,
        "source": source,
        "competitors": competitors,
    }
