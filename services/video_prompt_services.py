import json
import math
import re

from openai import OpenAI

from core.config import settings
from schema.video_prompt_schema import VideoBeat, VideoPromptRequest
from services.logger_services import logger
from services.s3_service import load_scraped_result_data

VIDEO_PROMPT_MODEL_FALLBACKS = ["gpt-4o-mini", "gpt-5.4-mini", "gpt-5.4-nano"]
INTRO_SECONDS = 8
CHAINING_SECONDS = 8
MAX_REVIEW_SNIPPETS = 8
CONTINUE_PREFIX = "Continue the same venue, lighting, and camera style."
ALLOWED_BEAT_PURPOSES = ("ambience", "food", "service", "social_proof")
NEGATIVE_REVIEW_MARKERS = (
    "bug",
    "bugs",
    "insect",
    "hair",
    "unsafe",
    "health and safety",
    "cancelled",
    "canceled",
    "never delivered",
    "refund",
    "disgusting",
    "sick",
    "complaint",
    "terrible",
    "worst",
    "it's ok",
    "its ok",
)


def _extract_json(raw_text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("OpenAI did not return a JSON object")
    return json.loads(cleaned[start : end + 1])


def _is_negative_comment(comment: str) -> bool:
    lowered = comment.lower()
    return any(marker in lowered for marker in NEGATIVE_REVIEW_MARKERS)


def _collect_positive_reviews(scraped_data: dict) -> list[str]:
    reviews: list[str] = []
    for platform in ("google_maps", "yelp", "tripadvisor"):
        for review in (scraped_data.get(platform, {}) or {}).get("reviews", []):
            rating = review.get("rating")
            comment = str(review.get("comment") or "").strip()
            if not comment or _is_negative_comment(comment):
                continue
            try:
                rating_value = float(rating)
            except (TypeError, ValueError):
                continue
            if rating_value >= 4.0:
                reviews.append(comment)
    seen: set[str] = set()
    unique_reviews: list[str] = []
    for review in reviews:
        normalized = review.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_reviews.append(review)
    return unique_reviews[:MAX_REVIEW_SNIPPETS]


def _safe_truncate(text: str, max_len: int = 180) -> str:
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 3]}..."


def _dedupe_sentences(text: str) -> str:
    sentences: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"(?<=[.!?])\s+", text.strip()):
        sentence = " ".join(part.split()).strip()
        if not sentence:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", sentence.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        sentences.append(sentence)
    return " ".join(sentences)


def _opening_scene_from_master(business_name: str, scene: str) -> str:
    cleaned = _dedupe_sentences(scene)
    cleaned = re.sub(
        rf"^create an {INTRO_SECONDS}-second cinematic intro clip for {re.escape(business_name)}\.?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    drop_markers = (
        "no on-screen text",
        "no logos",
        "no cta",
        "do not invent",
        "keep the exact same venue",
        "keep this exact venue",
        "later chained",
        "hospitality-ad look",
        "slow smooth camera",
        "warm natural lighting",
        "calls to action",
    )
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", cleaned):
        sentence = sentence.strip()
        if not sentence:
            continue
        lowered = sentence.lower()
        if any(marker in lowered for marker in drop_markers):
            continue
        kept.append(sentence)
    opening = " ".join(kept).strip()
    if opening and not opening.endswith("."):
        opening += "."
    if not opening:
        opening = (
            f"Show the welcoming storefront and entrance of {business_name} "
            "with guests arriving and a warm, inviting atmosphere."
        )
    return opening


def _finalize_master_prompt(business_name: str, scene: str) -> str:
    opening = _opening_scene_from_master(business_name, scene)
    return (
        f"Create an {INTRO_SECONDS}-second cinematic intro clip for {business_name}. "
        f"{opening} "
        "Use warm natural lighting, slow smooth camera motion, and a professional hospitality-ad look. "
        "Keep this exact venue, brand, colors, and atmosphere so later chained clips can continue this identity. "
        "No on-screen text, no overlay logos, no CTA, no other brand names, "
        "and do not invent a different location, cuisine, or store."
    )


def _cta_beat_prompt(cta_text: str, cta_url: str) -> str:
    return (
        f"{CONTINUE_PREFIX} "
        "End on a clean hero shot of the business. "
        f"Show this exact on-screen text: '{cta_text}'. "
        f"Show this exact URL: '{cta_url}'. "
        "No extra slogans and no other brand names."
    )


def _normalize_non_cta_beat(prompt: str, purpose: str, beat_number: int) -> tuple[str, str]:
    purpose = purpose if purpose in ALLOWED_BEAT_PURPOSES else ALLOWED_BEAT_PURPOSES[
        (beat_number - 1) % len(ALLOWED_BEAT_PURPOSES)
    ]
    text = " ".join(prompt.split()).strip()
    text = re.sub(
        r"show this exact on-screen text:.*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    if not text.lower().startswith(CONTINUE_PREFIX.lower()):
        text = f"{CONTINUE_PREFIX} {text}"
    return text.strip(), purpose


def _build_generation_prompt(
    payload: VideoPromptRequest,
    positive_reviews: list[str],
    beat_count: int,
) -> str:
    review_block = "\n".join(
        f"- {_safe_truncate(review)}" for review in positive_reviews
    ) or "- No positive review snippets available."

    return f"""You are a hospitality ad video director for {payload.business_name}.

Generate JSON only.

BUSINESS:
- business_name: {payload.business_name}
- cta_text: {payload.cta_text}
- cta_url: {payload.cta_url}
- intro_seconds: {INTRO_SECONDS}
- beat_count_required: {beat_count}

POSITIVE REVIEW THEMES (use these as inspiration, never quote complaints):
{review_block}

TASK:
1) master_prompt: one opening scene only (storefront/entrance/seating). 2-3 visual sentences. Do NOT repeat camera/CTA rules.
2) Write exactly {beat_count} beats.
   - Beats 1 to {max(beat_count - 1, 0)}: non-CTA only (ambience, food, service, social_proof).
   - Each of those must start with: "{CONTINUE_PREFIX}"
   - Weave in positive review themes (hot food, kind staff, loyal guests) without naming other brands.
3) Beat {beat_count} is the ONLY CTA beat. Use this exact CTA text and URL, do not rewrite them:
   - text: {payload.cta_text}
   - url: {payload.cta_url}

RULES:
- Do not mention {payload.cta_text} or {payload.cta_url} except in the last beat.
- Do not invent a different restaurant name.
- Return valid JSON:
{{
  "master_prompt": "string",
  "beats": [
    {{
      "beat_number": 1,
      "purpose": "ambience",
      "prompt": "string"
    }}
  ]
}}
"""


def generate_video_prompts(payload: VideoPromptRequest) -> dict:
    total_segments = math.ceil(payload.target_seconds / CHAINING_SECONDS)
    beat_count = max(total_segments - 1, 0)

    logger.info(
        "video_prompt: generating prompts — "
        f"business='{payload.business_name}', business_id='{payload.business_id}', "
        f"target_seconds={payload.target_seconds}, total_segments={total_segments}, "
        f"beat_count={beat_count}"
    )

    try:
        scraped_data = load_scraped_result_data(
            payload.business_name,
            payload.business_id,
        )
    except FileNotFoundError as exc:
        raise ValueError(
            "business_id does not exist for the provided business_name, "
            "or scraped data is missing. Run scrape API first."
        ) from exc
    except Exception as exc:
        logger.exception(
            "video_prompt: failed to load scraped data — "
            f"business='{payload.business_name}', business_id='{payload.business_id}', error={exc}"
        )
        raise ValueError(
            "Unable to load scraped data for the provided business_name/business_id."
        ) from exc

    positive_reviews: list[str] = _collect_positive_reviews(scraped_data)

    generation_prompt = _build_generation_prompt(payload, positive_reviews, beat_count)
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    selected_model = VIDEO_PROMPT_MODEL_FALLBACKS[-1]
    parsed: dict | None = None
    last_error: Exception | None = None

    for model in VIDEO_PROMPT_MODEL_FALLBACKS:
        selected_model = model
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a hospitality ad video director. "
                            "Return valid JSON only. Never duplicate instructions."
                        ),
                    },
                    {"role": "user", "content": generation_prompt},
                ],
                response_format={"type": "json_object"},
            )
            raw_text = response.choices[0].message.content or ""
            parsed = _extract_json(raw_text)
            break
        except Exception as exc:
            last_error = exc
            logger.warning(
                f"video_prompt: OpenAI model='{model}' failed — {exc}"
            )

    if parsed is None:
        raise RuntimeError(
            f"Video prompt generation failed for all OpenAI models — {last_error}"
        )

    master_prompt = str(parsed.get("master_prompt") or "").strip()
    if not master_prompt:
        raise ValueError("OpenAI response missing 'master_prompt'")
    master_prompt = _finalize_master_prompt(payload.business_name, master_prompt)

    raw_beats = parsed.get("beats", [])
    if not isinstance(raw_beats, list):
        raise ValueError("OpenAI response 'beats' must be a list")

    beats: list[VideoBeat] = []
    for idx, item in enumerate(raw_beats[:beat_count], start=1):
        purpose = str((item or {}).get("purpose") or "ambience").strip().lower()
        prompt = str((item or {}).get("prompt") or "").strip()
        if not prompt:
            continue
        if beat_count > 0 and idx < beat_count:
            prompt, purpose = _normalize_non_cta_beat(prompt, purpose, idx)
        beats.append(VideoBeat(beat_number=idx, purpose=purpose, prompt=prompt))

    while len(beats) < beat_count:
        beat_number = len(beats) + 1
        if beat_number == beat_count:
            beats.append(
                VideoBeat(
                    beat_number=beat_number,
                    purpose="cta",
                    prompt=_cta_beat_prompt(payload.cta_text, payload.cta_url),
                )
            )
            continue
        purpose = ALLOWED_BEAT_PURPOSES[(beat_number - 1) % len(ALLOWED_BEAT_PURPOSES)]
        beats.append(
            VideoBeat(
                beat_number=beat_number,
                purpose=purpose,
                prompt=(
                    f"{CONTINUE_PREFIX} Show a natural hospitality moment "
                    f"focused on {purpose.replace('_', ' ')}."
                ),
            )
        )

    if beat_count > 0:
        for idx in range(beat_count - 1):
            prompt, purpose = _normalize_non_cta_beat(
                beats[idx].prompt,
                beats[idx].purpose,
                idx + 1,
            )
            beats[idx] = VideoBeat(
                beat_number=idx + 1,
                purpose=purpose,
                prompt=prompt,
            )
        beats[-1] = VideoBeat(
            beat_number=beat_count,
            purpose="cta",
            prompt=_cta_beat_prompt(payload.cta_text, payload.cta_url),
        )

    result = {
        "status": "success",
        "business_name": payload.business_name,
        "business_id": payload.business_id,
        "model": selected_model,
        "target_seconds": payload.target_seconds,
        "intro_seconds": INTRO_SECONDS,
        "chaining_seconds": CHAINING_SECONDS,
        "total_segments": total_segments,
        "beat_count": beat_count,
        "positive_reviews_used": len(positive_reviews),
        "positive_review_snippets": positive_reviews,
        "master_prompt": master_prompt,
        "beats": [beat.model_dump() for beat in beats],
    }
    logger.info(
        "video_prompt: generated successfully — "
        f"business='{payload.business_name}', beats={len(beats)}"
    )
    return result
