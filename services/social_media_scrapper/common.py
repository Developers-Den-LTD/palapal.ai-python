import re

from services.logger_services import logger
from services.model_loader import load_sentiment_model
from services.scrapper_services import get_dataset_id

ADDRESS_PATTERN = re.compile(
    r"\d{1,5}[\w\s,.#-]{5,120}?(?:street|st\.?|road|rd\.?|avenue|ave\.?|"
    r"boulevard|blvd\.?|lane|ln\.?|drive|dr\.?|way|court|ct\.?|"
    r"place|pl\.?|highway|hwy\.?)\b[\w\s,.#-]{0,80}",
    re.IGNORECASE,
)


def log_section(platform_tag: str, title: str) -> None:
    logger.info(f"{platform_tag}: {'=' * 60}")
    logger.info(f"{platform_tag}: {title}")
    logger.info(f"{platform_tag}: {'=' * 60}")


def list_actor_items(run) -> list[dict]:
    from services.scrapper_services import client

    dataset_id = get_dataset_id(run)
    if not dataset_id:
        return []
    return list(client.dataset(dataset_id).list_items().items)


def unique_strings(values: list) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def extract_address_from_bio(biography: str | None) -> str | None:
    if not biography or not biography.strip():
        return None
    match = ADDRESS_PATTERN.search(biography)
    return match.group(0).strip() if match else None


def analyze_tone_of_voice(
    texts: list[str],
    *,
    platform_tag: str,
    source_label: str,
) -> dict:
    """Shared sentiment-based tone analysis for Instagram, Facebook, and Twitter text."""
    cleaned = [text.strip() for text in texts if text and text.strip()]
    if not cleaned:
        logger.info(f"{platform_tag}: no text available for tone-of-voice analysis")
        return {
            "summary": f"No {source_label} available to analyze tone of voice.",
            "dominant_tone": None,
            "positive": 0,
            "neutral": 0,
            "negative": 0,
            "total_texts": 0,
        }

    logger.info(
        f"{platform_tag}: analyzing tone of voice from "
        f"{len(cleaned)} text(s) ({source_label})"
    )

    model = load_sentiment_model()
    positive = neutral = negative = 0

    for text in cleaned:
        try:
            prediction = model(text[:512])[0]
            label = str(prediction.get("label", "")).lower()
            if "positive" in label:
                positive += 1
            elif "negative" in label:
                negative += 1
            else:
                neutral += 1
        except Exception as exc:
            logger.warning(f"{platform_tag}: sentiment failed — {exc}")
            neutral += 1

    dominant = "neutral"
    if positive >= neutral and positive >= negative:
        dominant = "positive"
    elif negative > positive and negative >= neutral:
        dominant = "negative"

    logger.info(
        f"{platform_tag}: tone result — dominant={dominant}, "
        f"positive={positive}, neutral={neutral}, negative={negative}"
    )

    return {
        "summary": (
            f"Overall tone appears {dominant} based on {source_label}."
        ),
        "dominant_tone": dominant,
        "positive": positive,
        "neutral": neutral,
        "negative": negative,
        "total_texts": positive + neutral + negative,
    }
