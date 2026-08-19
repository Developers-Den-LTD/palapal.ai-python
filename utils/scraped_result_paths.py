import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_BASE_PATH = BASE_DIR / "scraping_results"
DDI_SCORE_BASE_PATH = BASE_DIR / "DDI_score"
DDI_SCORE_RESULT_FILENAME = "Result.json"
# Local folder where generated llms.txt files are saved (llms.txt pipeline output).
LLMS_TXT_OUTPUT_BASE_PATH = BASE_DIR / "llms_txt_outputs"
LLMS_TXT_FILENAME = "llms.txt"
LLMS_TXT_METADATA_FILENAME = "generation_metadata.json"
REVIEW_REPLIES_BASE_PATH = BASE_DIR / "Review_Replies"
VIDEO_STUDIO_BASE_PATH = BASE_DIR / "Video_Studio"
VIDEO_STUDIO_VIDEO_FILENAME = "video.mp4"
VIDEO_STUDIO_RESULT_FILENAME = "Result.json"


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


def _slug_from_website_url(website_url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(website_url.strip())
    host = (parsed.netloc or parsed.path or "website").lower()
    if host.startswith("www."):
        host = host[4:]
    return slugify_folder_name(host.replace(".", "_"))


def get_llms_txt_output_folder(
    website_url: str,
    business_id: str | int | None = None,
) -> Path:
    """Folder for one site's generated llms.txt (e.g. llms_txt_outputs/kfc_com_pk_42/)."""
    folder_slug = _slug_from_website_url(website_url)
    if business_id is not None and str(business_id).strip():
        folder_slug = f"{folder_slug}_{str(business_id).strip()}"
    return LLMS_TXT_OUTPUT_BASE_PATH / folder_slug


def get_llms_txt_output_path(
    website_url: str,
    business_id: str | int | None = None,
) -> Path:
    return get_llms_txt_output_folder(website_url, business_id) / LLMS_TXT_FILENAME


def get_llms_txt_metadata_path(
    website_url: str,
    business_id: str | int | None = None,
) -> Path:
    return get_llms_txt_output_folder(website_url, business_id) / LLMS_TXT_METADATA_FILENAME


def get_review_replies_result_path(
    business_name: str,
    business_id: str | int | None = None,
) -> Path:
    folder_slug = build_scrape_storage_slug(business_name, business_id)
    return REVIEW_REPLIES_BASE_PATH / f"{folder_slug}.json"


def get_video_studio_folder(
    business_name: str,
    business_id: str | int | None = None,
) -> Path:
    folder_slug = build_scrape_storage_slug(business_name, business_id)
    return VIDEO_STUDIO_BASE_PATH / folder_slug


def get_video_studio_video_path(
    business_name: str,
    business_id: str | int | None = None,
) -> Path:
    return get_video_studio_folder(business_name, business_id) / VIDEO_STUDIO_VIDEO_FILENAME


def get_video_studio_result_path(
    business_name: str,
    business_id: str | int | None = None,
) -> Path:
    return get_video_studio_folder(business_name, business_id) / VIDEO_STUDIO_RESULT_FILENAME
