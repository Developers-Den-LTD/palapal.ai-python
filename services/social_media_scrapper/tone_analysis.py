import asyncio

from openai import OpenAI

from core.config import settings
from services.logger_services import logger

LOG_TAG = "socialmedia [tone_analysis]"

_STATUS_THRESHOLDS = [
    (85, "Warm & Welcoming"),
    (70, "Positive & Friendly"),
    (55, "Balanced & Neutral"),
    (40, "Mixed Signals"),
    (0,  "Needs Attention"),
]


def _extract_tone(platform_data: dict | None) -> dict | None:
    """Pull tone_of_voice block from a platform dict, return None if absent."""
    if not platform_data:
        return None
    tone = platform_data.get("tone_of_voice") or {}
    if not tone or not isinstance(tone, dict):
        return None
    total = tone.get("total_texts", 0)
    if not isinstance(total, (int, float)) or total <= 0:
        return None
    return tone


def _positive_pct(tone: dict) -> float:
    total = tone.get("total_texts", 0)
    positive = tone.get("positive", 0)
    if total <= 0:
        return 0.0
    return round((positive / total) * 100, 1)


def _overall_status(average_pct: float) -> str:
    for threshold, label in _STATUS_THRESHOLDS:
        if average_pct >= threshold:
            return label
    return "Needs Attention"


def _build_insight_prompt(platform_tones: dict[str, dict]) -> str:
    lines = []
    for platform, tone in platform_tones.items():
        pct = _positive_pct(tone)
        dominant = tone.get("dominant_tone", "unknown")
        lines.append(
            f"{platform.capitalize()}: dominant_tone={dominant}, positive={pct}%"
        )
    tone_summary = "\n".join(lines)

    return (
        "You are a social media brand consultant.\n\n"
        "Platform tone breakdown:\n"
        f"{tone_summary}\n\n"
        "Write ONE short, actionable insight (max 25 words) about brand tone "
        "consistency across these platforms. Be direct and specific."
    )


def _call_openai_insight(prompt: str) -> str:
    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a social media brand tone analyst.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=60,
            temperature=0.4,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.warning(f"{LOG_TAG}: GPT-4o-mini insight call failed — {exc}")
        return "Unable to generate AI insight at this time."


async def build_tone_analysis(
    instagram: dict | None,
    facebook: dict | None,
    twitter: dict | None,
) -> dict:
    """
    Build tone analysis across Instagram, Facebook, and Twitter.

    Returns a dict with:
      - platforms: per-platform positive %, counts, dominant tone
      - average_positive_pct: mean positive % across platforms with data
      - overall_status: rule-based label
      - ai_insight: GPT-4o-mini sentence
    """
    platform_inputs = {
        "instagram": instagram,
        "facebook": facebook,
        "twitter": twitter,
    }

    platform_tones: dict[str, dict] = {}
    platform_results: dict[str, dict] = {}

    for platform, data in platform_inputs.items():
        tone = _extract_tone(data)
        if tone is not None:
            platform_tones[platform] = tone
            pct = _positive_pct(tone)
            platform_results[platform] = {
                "dominant_tone": tone.get("dominant_tone"),
                "positive_pct": pct,
                "positive": tone.get("positive", 0),
                "neutral": tone.get("neutral", 0),
                "negative": tone.get("negative", 0),
                "total_texts": tone.get("total_texts", 0),
                "summary": tone.get("summary", ""),
            }
        else:
            platform_results[platform] = None

    if not platform_tones:
        logger.info(f"{LOG_TAG}: no tone_of_voice data found on any platform")
        return {
            "overall_status": None,
            "average_positive_pct": None,
            "platforms": platform_results,
            "ai_insight": "No tone of voice data available to analyse.",
        }

    pcts = [_positive_pct(tone) for tone in platform_tones.values()]
    average_pct = round(sum(pcts) / len(pcts), 1)
    overall = _overall_status(average_pct)

    logger.info(
        f"{LOG_TAG}: platforms with tone data={list(platform_tones.keys())}, "
        f"average_positive_pct={average_pct}, overall_status='{overall}'"
    )

    prompt = _build_insight_prompt(platform_tones)
    ai_insight = await asyncio.to_thread(_call_openai_insight, prompt)

    logger.info(f"{LOG_TAG}: ai_insight='{ai_insight}'")

    return {
        "overall_status": overall,
        "average_positive_pct": average_pct,
        "platforms": platform_results,
        "ai_insight": ai_insight,
    }
