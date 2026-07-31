import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_BASE_PATH = BASE_DIR / "scraping_results"
DDI_SCORE_BASE_PATH = BASE_DIR / "DDI_score"
DDI_SCORE_RESULT_FILENAME = "Result.json"


def slugify_folder_name(name: str) -> str:
    """Converts a business name into a safe, lower-case filesystem directory name."""
    if not name:
        return "unknown_business"
    name = name.lower().strip()
    name = re.sub(re.escape(" "), "_", name)
    name = re.sub(r"[^\w\-]", "", name)
    return name or "unknown_business"


def build_scrape_storage_slug(
    business_name: str,
    business_id: str | int | None = None,
) -> str:
    slug = slugify_folder_name(business_name)
    if business_id is not None and str(business_id).strip():
        return f"{slug}_{str(business_id).strip()}"
    return slug


def get_scraped_result_folder(
    business_name: str,
    business_id: str | int | None = None,
) -> Path:
    folder_slug = build_scrape_storage_slug(business_name, business_id)
    return RESULTS_BASE_PATH / folder_slug


def get_scraped_result_path(
    business_name: str,
    business_id: str | int | None = None,
) -> Path:
    return get_scraped_result_folder(business_name, business_id) / "scraped_result.json"


def get_ddi_score_result_folder(
    business_name: str,
    business_id: str | int | None = None,
) -> Path:
    folder_slug = build_scrape_storage_slug(business_name, business_id)
    return DDI_SCORE_BASE_PATH / folder_slug


def get_ddi_score_result_path(
    business_name: str,
    business_id: str | int | None = None,
) -> Path:
    return get_ddi_score_result_folder(business_name, business_id) / DDI_SCORE_RESULT_FILENAME
