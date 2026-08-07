import json
import os
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, NoCredentialsError

from core.config import settings
from services.logger_services import logger
from utils.scraped_result_paths import (
    build_scrape_storage_slug,
    get_ddi_score_result_path,
    get_review_replies_result_path,
    get_scraped_result_path,
)

S3_KEY_PREFIX = "scraping_results"
SCRAPED_RESULT_FILENAME = "scraped_result.json"
DDI_SCORE_KEY_PREFIX = "DDI_score"
DDI_SCORE_RESULT_FILENAME = "Result.json"
REVIEW_REPLIES_KEY_PREFIX = "Review_Replies"


def _get_s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION,
        config=Config(signature_version="s3v4"),
    )


def get_s3_key(business_name: str, business_id: str | int | None = None) -> str:
    folder_slug = build_scrape_storage_slug(business_name, business_id)
    return f"{S3_KEY_PREFIX}/{folder_slug}/{SCRAPED_RESULT_FILENAME}"


def get_ddi_score_s3_key(
    business_name: str,
    business_id: str | int | None = None,
) -> str:
    folder_slug = build_scrape_storage_slug(business_name, business_id)
    return f"{DDI_SCORE_KEY_PREFIX}/{folder_slug}/{DDI_SCORE_RESULT_FILENAME}"


def get_review_replies_s3_key(
    business_name: str,
    business_id: str | int | None = None,
) -> str:
    folder_slug = build_scrape_storage_slug(business_name, business_id)
    return f"{REVIEW_REPLIES_KEY_PREFIX}/{folder_slug}"


def upload_json_to_s3(*, s3_key: str, data: dict) -> bool:
    """
    Upload a JSON payload to S3 at a given key.
    Overwrites any existing object at the same key.
    """
    try:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        _get_s3_client().put_object(
            Bucket=settings.AWS_S3_BUCKET,
            Key=s3_key,
            Body=body,
            ContentType="application/json",
        )
        logger.info(
            f"s3_service: JSON upload completed successfully — "
            f"bucket={settings.AWS_S3_BUCKET}, key={s3_key}"
        )
        return True
    except ClientError as exc:
        logger.error(f"s3_service: JSON upload failed for key={s3_key} — {exc}")
        return False
    except NoCredentialsError as exc:
        logger.error(f"s3_service: invalid or missing AWS credentials — {exc}")
        return False
    except Exception as exc:
        logger.error(f"s3_service: unexpected JSON upload error for key={s3_key} — {exc}")
        return False


def upload_ddi_score_result_to_s3(
    business_name: str,
    result: dict,
    business_id: str | int | None = None,
) -> bool:
    """
    Upload the DDI score result JSON to:
      DDI_score/<business_slug>[_business_id]/Result.json
    """
    s3_key = get_ddi_score_s3_key(business_name, business_id)
    return upload_json_to_s3(s3_key=s3_key, data=result)


def upload_review_replies_result_to_s3(
    business_name: str,
    result: dict,
    business_id: str | int | None = None,
) -> bool:
    """
    Upload review reply results to:
      Review_Replies/<business_slug>[_business_id]
    """
    s3_key = get_review_replies_s3_key(business_name, business_id)
    return upload_json_to_s3(s3_key=s3_key, data=result)


def business_exists_in_s3(
    business_name: str,
    business_id: str | int | None = None,
) -> bool:
    s3_key = get_s3_key(business_name, business_id)
    try:
        _get_s3_client().head_object(
            Bucket=settings.AWS_S3_BUCKET,
            Key=s3_key,
        )
        logger.info(f"s3_service: business data found in S3 — key={s3_key}")
        return True
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey", "NotFound"):
            logger.info(f"s3_service: business data not found in S3 — key={s3_key}")
            return False
        logger.error(f"s3_service: S3 head_object failed for key={s3_key} — {exc}")
        return False
    except NoCredentialsError as exc:
        logger.error(f"s3_service: invalid or missing AWS credentials — {exc}")
        return False
    except Exception as exc:
        logger.error(f"s3_service: unexpected error checking S3 for key={s3_key} — {exc}")
        return False


def download_scraped_result_from_s3(
    business_name: str,
    local_path: Path | None = None,
    business_id: str | int | None = None,
) -> bool:
    local_path = local_path or get_scraped_result_path(business_name, business_id)
    s3_key = get_s3_key(business_name, business_id)
    temp_path = local_path.with_suffix(".json.tmp")

    try:
        logger.info(f"s3_service: downloading from S3 — key={s3_key}")
        response = _get_s3_client().get_object(
            Bucket=settings.AWS_S3_BUCKET,
            Key=s3_key,
        )
        body = response["Body"].read()

        local_path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_path, "wb") as file:
            file.write(body)
        os.replace(temp_path, local_path)

        logger.info(f"s3_service: download completed successfully — {local_path}")
        return True
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey", "NotFound"):
            logger.warning(f"s3_service: object not found in S3 — key={s3_key}")
        else:
            logger.error(f"s3_service: S3 download failed for key={s3_key} — {exc}")
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False
    except (NoCredentialsError, OSError) as exc:
        logger.error(f"s3_service: download failed for '{business_name}' — {exc}")
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False
    except Exception as exc:
        logger.error(
            f"s3_service: unexpected download error for '{business_name}' — {exc}"
        )
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False


def upload_scraped_result_to_s3(
    business_name: str,
    local_file_path: Path | None = None,
    business_id: str | int | None = None,
) -> bool:
    local_path = local_file_path or get_scraped_result_path(business_name, business_id)
    s3_key = get_s3_key(business_name, business_id)

    if not local_path.exists():
        logger.error(f"s3_service: cannot upload, local file missing — {local_path}")
        return False

    try:
        logger.info(f"s3_service: uploading business data to S3 — key={s3_key}")
        with open(local_path, "rb") as file:
            _get_s3_client().put_object(
                Bucket=settings.AWS_S3_BUCKET,
                Key=s3_key,
                Body=file,
                ContentType="application/json",
            )
        logger.info(
            f"s3_service: upload completed successfully — "
            f"bucket={settings.AWS_S3_BUCKET}, key={s3_key}"
        )
        return True
    except ClientError as exc:
        logger.error(f"s3_service: S3 upload failed for key={s3_key} — {exc}")
        return False
    except NoCredentialsError as exc:
        logger.error(f"s3_service: invalid or missing AWS credentials — {exc}")
        return False
    except OSError as exc:
        logger.error(f"s3_service: failed to read local file for upload — {exc}")
        return False
    except Exception as exc:
        logger.error(f"s3_service: unexpected upload error for '{business_name}' — {exc}")
        return False


def ensure_scraped_result_available(
    business_name: str,
    business_id: str | int | None = None,
) -> Path:
    """
    Return local path to scraped_result.json.
    Uses local scraping_results first; downloads from S3 into the same path if missing.
    """
    business_name = business_name.strip()
    local_path = get_scraped_result_path(business_name, business_id)

    if local_path.exists():
        logger.info(f"s3_service: using local scraped data — {local_path}")
        return local_path

    logger.info(
        f"s3_service: business not found locally, checking S3 — business='{business_name}'"
    )

    if not business_exists_in_s3(business_name, business_id):
        raise FileNotFoundError(f"No scraped data found for '{business_name}'")

    if not download_scraped_result_from_s3(business_name, local_path, business_id):
        raise FileNotFoundError(
            f"Failed to download scraped data for '{business_name}' from S3"
        )

    return local_path


def delete_scraped_result_from_s3(business_name: str) -> dict:
    """
    Delete scraped_result.json from S3 for a business.
    Returns dict with keys: existed, deleted, error.
    """
    business_name = business_name.strip()
    s3_key = get_s3_key(business_name)
    result = {"existed": False, "deleted": False, "error": None, "s3_key": s3_key}

    if not business_exists_in_s3(business_name):
        logger.info(
            f"s3_service: nothing to delete in S3 for business='{business_name}' — key={s3_key}"
        )
        return result

    result["existed"] = True
    try:
        logger.info(f"s3_service: deleting from S3 — key={s3_key}")
        _get_s3_client().delete_object(
            Bucket=settings.AWS_S3_BUCKET,
            Key=s3_key,
        )
        result["deleted"] = True
        logger.info(
            f"s3_service: deleted successfully — "
            f"bucket={settings.AWS_S3_BUCKET}, key={s3_key}"
        )
        return result
    except ClientError as exc:
        error_message = f"S3 delete failed for key={s3_key} — {exc}"
        logger.error(f"s3_service: {error_message}")
        result["error"] = error_message
        return result
    except NoCredentialsError as exc:
        error_message = f"invalid or missing AWS credentials — {exc}"
        logger.error(f"s3_service: {error_message}")
        result["error"] = error_message
        return result
    except Exception as exc:
        error_message = f"unexpected S3 delete error for '{business_name}' — {exc}"
        logger.error(f"s3_service: {error_message}")
        result["error"] = error_message
        return result


def load_scraped_result_data(
    business_name: str,
    business_id: str | int | None = None,
) -> dict:
    """
    Load scraped_result.json for a business.
    Checks local scraping_results first; downloads from S3 into the same path if missing.
    """
    local_path = ensure_scraped_result_available(business_name, business_id)
    with open(local_path, "r", encoding="utf-8") as file:
        return json.load(file)


def ddi_score_exists_in_s3(
    business_name: str,
    business_id: str | int | None = None,
) -> bool:
    s3_key = get_ddi_score_s3_key(business_name, business_id)
    try:
        _get_s3_client().head_object(
            Bucket=settings.AWS_S3_BUCKET,
            Key=s3_key,
        )
        logger.info(f"s3_service: DDI score found in S3 — key={s3_key}")
        return True
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey", "NotFound"):
            logger.info(f"s3_service: DDI score not found in S3 — key={s3_key}")
            return False
        logger.error(f"s3_service: S3 head_object failed for key={s3_key} — {exc}")
        return False
    except NoCredentialsError as exc:
        logger.error(f"s3_service: invalid or missing AWS credentials — {exc}")
        return False
    except Exception as exc:
        logger.error(
            f"s3_service: unexpected error checking DDI score in S3 for key={s3_key} — {exc}"
        )
        return False


def download_ddi_score_result_from_s3(
    business_name: str,
    local_path: Path | None = None,
    business_id: str | int | None = None,
) -> bool:
    local_path = local_path or get_ddi_score_result_path(business_name, business_id)
    s3_key = get_ddi_score_s3_key(business_name, business_id)
    temp_path = local_path.with_suffix(".json.tmp")

    try:
        logger.info(f"s3_service: downloading DDI score from S3 — key={s3_key}")
        response = _get_s3_client().get_object(
            Bucket=settings.AWS_S3_BUCKET,
            Key=s3_key,
        )
        body = response["Body"].read()

        local_path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_path, "wb") as file:
            file.write(body)
        os.replace(temp_path, local_path)

        logger.info(f"s3_service: DDI score download completed — {local_path}")
        return True
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey", "NotFound"):
            logger.warning(f"s3_service: DDI score not found in S3 — key={s3_key}")
        else:
            logger.error(f"s3_service: DDI score download failed for key={s3_key} — {exc}")
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False
    except (NoCredentialsError, OSError) as exc:
        logger.error(f"s3_service: DDI score download failed for '{business_name}' — {exc}")
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False
    except Exception as exc:
        logger.error(
            f"s3_service: unexpected DDI score download error for '{business_name}' — {exc}"
        )
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False


def review_replies_exists_in_s3(
    business_name: str,
    business_id: str | int | None = None,
) -> bool:
    s3_key = get_review_replies_s3_key(business_name, business_id)
    try:
        _get_s3_client().head_object(
            Bucket=settings.AWS_S3_BUCKET,
            Key=s3_key,
        )
        logger.info(f"s3_service: review replies found in S3 — key={s3_key}")
        return True
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey", "NotFound"):
            return False
        logger.error(f"s3_service: S3 head_object failed for key={s3_key} — {exc}")
        return False
    except NoCredentialsError as exc:
        logger.error(f"s3_service: invalid or missing AWS credentials — {exc}")
        return False
    except Exception as exc:
        logger.error(
            f"s3_service: unexpected error checking review replies in S3 for key={s3_key} — {exc}"
        )
        return False


def download_review_replies_result_from_s3(
    business_name: str,
    local_path: Path | None = None,
    business_id: str | int | None = None,
) -> bool:
    local_path = local_path or get_review_replies_result_path(business_name, business_id)
    s3_key = get_review_replies_s3_key(business_name, business_id)
    temp_path = local_path.with_suffix(".json.tmp")

    try:
        logger.info(f"s3_service: downloading review replies from S3 — key={s3_key}")
        response = _get_s3_client().get_object(
            Bucket=settings.AWS_S3_BUCKET,
            Key=s3_key,
        )
        body = response["Body"].read()

        local_path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_path, "wb") as file:
            file.write(body)
        os.replace(temp_path, local_path)

        logger.info(f"s3_service: review replies download completed — {local_path}")
        return True
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey", "NotFound"):
            logger.warning(f"s3_service: review replies not found in S3 — key={s3_key}")
        else:
            logger.error(
                f"s3_service: review replies download failed for key={s3_key} — {exc}"
            )
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False
    except (NoCredentialsError, OSError) as exc:
        logger.error(f"s3_service: review replies download failed for '{business_name}' — {exc}")
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False
    except Exception as exc:
        logger.error(
            f"s3_service: unexpected review replies download error for '{business_name}' — {exc}"
        )
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False


def load_review_replies_result_data(
    business_name: str,
    business_id: str | int | None = None,
) -> dict | None:
    local_path = get_review_replies_result_path(business_name, business_id)

    if not local_path.exists() and review_replies_exists_in_s3(business_name, business_id):
        download_review_replies_result_from_s3(business_name, local_path, business_id)

    if not local_path.exists():
        return None

    with open(local_path, "r", encoding="utf-8") as file:
        return json.load(file)
