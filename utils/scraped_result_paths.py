import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_BASE_PATH = BASE_DIR / "scraping_results"


def slugify_folder_name(name: str) -> str:
    """Converts a business name into a safe, lower-case filesystem directory name."""
    if not name:
        return "unknown_business"
    name = name.lower().strip()
    name = re.sub(re.escape(" "), "_", name)
    name = re.sub(r"[^\w\-]", "", name)
    return name or "unknown_business"


def get_scraped_result_path(business_name: str) -> Path:
    folder_slug = slugify_folder_name(business_name)
    return RESULTS_BASE_PATH / folder_slug / "scraped_result.json"
