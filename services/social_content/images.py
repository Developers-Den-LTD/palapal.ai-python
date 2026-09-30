"""
Image generation for AI social content.

`generate_image` tries the chosen provider's model up to MAX_IMAGE_ATTEMPTS times
(no retry after a safety block), then the other provider's model of the same
quality once. At most 3 paid image calls per job. A later video module exposes
`generate_video` with the same result shape.
"""

import base64

import openai
from google import genai
from google.genai import types
from openai import OpenAI

from core.config import settings
from services.logger_services import logger
from services.social_content.constants import (
    GEMINI_IMAGE_SIZES,
    IMAGE_MODELS,
    IMAGE_TIMEOUT_SECONDS,
    MAX_IMAGE_ATTEMPTS,
    OPENAI_IMAGE_QUALITY,
    OPENAI_IMAGE_SIZES,
)
from services.social_content.reference import image_dimensions, sniff_image_type

_GEMINI_BLOCK_REASONS = ("SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII")
_REFERENCE_FILENAMES = {"image/png": "reference.png", "image/jpeg": "reference.jpg", "image/webp": "reference.webp"}


def _other_provider(provider: str) -> str:
    return "openai" if provider == "gemini" else "gemini"


def _gemini_image(
    prompt: str, model: str, quality: str, aspect_ratio: str, reference: dict | None
) -> tuple[bytes | None, str | None]:
    """Return (image bytes, None) or (None, block reason)."""
    client = genai.Client(
        api_key=settings.Gemini_API_KEY,
        http_options=types.HttpOptions(timeout=IMAGE_TIMEOUT_SECONDS * 1000),
    )
    contents: list = [prompt]
    if reference:
        contents.append(types.Part.from_bytes(data=reference["data"], mime_type=reference["mime_type"]))

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=aspect_ratio,
                image_size=GEMINI_IMAGE_SIZES[quality],
            ),
        ),
    )

    feedback = getattr(response, "prompt_feedback", None)
    if feedback and getattr(feedback, "block_reason", None):
        return None, str(feedback.block_reason)

    for candidate in response.candidates or []:
        for part in (candidate.content.parts if candidate.content else None) or []:
            inline = getattr(part, "inline_data", None)
            if inline and inline.data:
                return inline.data, None
        reason = str(getattr(candidate, "finish_reason", "") or "")
        if any(block in reason for block in _GEMINI_BLOCK_REASONS):
            return None, reason

    raise ValueError("Gemini returned no image")


def _openai_image(
    prompt: str, model: str, quality: str, aspect_ratio: str, reference: dict | None
) -> tuple[bytes | None, str | None]:
    """Return (image bytes, None) or (None, block reason)."""
    # max_retries=0: our own attempt loop is the paid-call ceiling; SDK retries would multiply it.
    client = OpenAI(
        api_key=settings.OPENAI_API_KEY,
        max_retries=0,
        timeout=IMAGE_TIMEOUT_SECONDS,
    )
    options = {
        "model": model,
        "prompt": prompt,
        "size": OPENAI_IMAGE_SIZES[aspect_ratio],
        "quality": OPENAI_IMAGE_QUALITY[quality],
        "output_format": "png",
    }
    try:
        if reference:
            filename = _REFERENCE_FILENAMES[reference["mime_type"]]
            response = client.images.edit(
                image=(filename, reference["data"], reference["mime_type"]),
                **options,
            )
        else:
            response = client.images.generate(**options)
    except openai.BadRequestError as exc:
        if getattr(exc, "code", None) == "moderation_blocked":
            return None, "moderation_blocked"
        raise

    b64 = response.data[0].b64_json if response.data else None
    if not b64:
        raise ValueError("OpenAI returned no image")
    return base64.b64decode(b64), None


_PROVIDER_CALLS = {"gemini": _gemini_image, "openai": _openai_image}


def generate_image(
    *,
    prompt: str,
    provider: str,
    quality: str,
    aspect_ratio: str,
    reference: dict | None,
) -> dict:
    """
    Return {"status", "data", "mime_type", "width", "height", "provider", "model",
    "fallback_used", "safety_blocked", "attempts"}. status is "success" or "error";
    this function never raises for a provider failure.
    """
    attempts: list[dict] = []
    safety_blocked = False
    plan = ((provider, MAX_IMAGE_ATTEMPTS), (_other_provider(provider), 1))

    for current, max_attempts in plan:
        model = IMAGE_MODELS[current][quality]
        for attempt in range(1, max_attempts + 1):
            logger.info(
                "social_content [image]: calling — "
                f"provider='{current}', model='{model}', attempt={attempt}/{max_attempts}, "
                f"aspect_ratio='{aspect_ratio}', quality='{quality}', has_reference={bool(reference)}"
            )
            try:
                data, block_reason = _PROVIDER_CALLS[current](
                    prompt, model, quality, aspect_ratio, reference
                )
            except Exception as exc:
                # Vendor SDKs raise their own error types; retry, then fall back.
                attempts.append(
                    {"provider": current, "model": model, "attempt": attempt, "status": "error", "error": str(exc)}
                )
                logger.warning(
                    "social_content [image]: call failed — "
                    f"provider='{current}', model='{model}', attempt={attempt}, error={exc}"
                )
                continue

            if block_reason:
                safety_blocked = True
                attempts.append(
                    {"provider": current, "model": model, "attempt": attempt, "status": "blocked", "error": block_reason}
                )
                logger.warning(
                    "social_content [image]: safety block, not retrying this provider — "
                    f"provider='{current}', model='{model}', reason='{block_reason}'"
                )
                break

            mime_type = sniff_image_type(data)
            if mime_type is None:
                attempts.append(
                    {"provider": current, "model": model, "attempt": attempt, "status": "error", "error": "unreadable image bytes"}
                )
                logger.warning(
                    "social_content [image]: provider returned bytes that are not a PNG, JPEG or WebP — "
                    f"provider='{current}', model='{model}', bytes={len(data)}"
                )
                continue

            dimensions = image_dimensions(data)
            if dimensions is None:
                logger.warning(
                    "social_content [image]: could not read image dimensions — "
                    f"provider='{current}', mime_type='{mime_type}'"
                )
            fallback_used = current != provider
            if fallback_used:
                logger.warning(
                    "social_content [image]: fallback used — "
                    f"from='{provider}', to='{current}', model='{model}'"
                )
            attempts.append({"provider": current, "model": model, "attempt": attempt, "status": "success"})
            logger.info(
                "social_content [image]: generated — "
                f"provider='{current}', model='{model}', bytes={len(data)}, "
                f"mime_type='{mime_type}', dimensions={dimensions}"
            )
            return {
                "status": "success",
                "data": data,
                "mime_type": mime_type,
                "width": dimensions[0] if dimensions else None,
                "height": dimensions[1] if dimensions else None,
                "provider": current,
                "model": model,
                "fallback_used": fallback_used,
                "safety_blocked": safety_blocked,
                "attempts": attempts,
            }

    logger.error(
        "social_content [image]: all attempts failed — "
        f"provider_requested='{provider}', paid_calls={len(attempts)}, safety_blocked={safety_blocked}"
    )
    return {
        "status": "error",
        "data": None,
        "mime_type": None,
        "width": None,
        "height": None,
        "provider": None,
        "model": None,
        "fallback_used": False,
        "safety_blocked": safety_blocked,
        "attempts": attempts,
    }
