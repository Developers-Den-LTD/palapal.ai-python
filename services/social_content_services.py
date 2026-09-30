"""
AI social content (Extension points 13 and 14): guideline suggestions, post ideas
and image generation.

1. suggest_guidelines: one text call pre-fills the #14 guidelines from the profile.
2. suggest_ideas: one text call returns 5 post ideas.
3. generate_content (background): optional reference photo, one plan call (image
   prompt, captions, hashtags, overlay headline), one image with retries and a
   cross-provider fallback, image to S3, caption checks with one repair call,
   result.json to S3. The route posts the returned dict to the webhook.
"""

import uuid
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from schema.social_content_schema import (
    CONTENT_TYPES,
    PLATFORMS,
    GenerateRequest,
    GuidelineSuggestRequest,
    Idea,
    IdeasRequest,
    SocialContentGuidelines,
)
from services.logger_services import logger
from services.s3_service import (
    generate_presigned_get_url,
    get_social_content_job_prefix,
    upload_bytes_to_s3,
    upload_json_to_s3,
)
from services.social_content.constants import (
    CONTENT_TYPE_RECIPES,
    IDEAS_COUNT,
    NO_TEXT_INSTRUCTION,
    OVERLAY_AREA_NAMES,
    PLATFORM_SPECS,
    PRESIGNED_URL_SECONDS,
    REFERENCE_USE_INSTRUCTIONS,
    X_TARGET_CHARS,
)
from services.social_content.context import build_context_card, user_input
from services.social_content.images import generate_image
from services.social_content.reference import download_reference_image
from services.social_content.text_llm import generate_json
from services.social_content.validators import (
    build_hashtag_candidates,
    check_invented_facts,
    hashtag_cap,
    validate_caption,
)

_IMAGE_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_GUIDELINE_FIELDS = (
    "brand_tone",
    "writing_style",
    "target_audience",
    "post_length",
    "emoji_usage",
    "hashtags",
    "cta",
    "restricted_words",
    "required_terms",
)
_STRING_LIST = {"type": "array", "items": {"type": "string"}}
_CAPTION_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "platform": {"type": "string", "enum": list(PLATFORMS)},
        "text": {"type": "string"},
        "title": {"type": "string"},
        "hashtags": _STRING_LIST,
        "cta": {"type": "string"},
    },
    "required": ["platform", "text", "title", "hashtags", "cta"],
    "additionalProperties": False,
}
_GUIDELINES_SCHEMA = {
    "type": "object",
    "properties": {
        "brand_tone": {"type": "string"},
        "writing_style": {"type": "string"},
        "target_audience": {"type": "string"},
        "post_length": {"type": "string", "enum": ["short", "medium", "long"]},
        "emoji_usage": {"type": "string", "enum": ["none", "minimal", "moderate", "liberal"]},
        "hashtags": {
            "type": "object",
            "properties": {
                "max_count": {"type": "integer"},
                "always_include": _STRING_LIST,
                "never_include": _STRING_LIST,
            },
            "required": ["max_count", "always_include", "never_include"],
            "additionalProperties": False,
        },
        "cta": {
            "type": "object",
            "properties": {"preferred": _STRING_LIST, "required": {"type": "boolean"}},
            "required": ["preferred", "required"],
            "additionalProperties": False,
        },
        "restricted_words": _STRING_LIST,
        "required_terms": _STRING_LIST,
        "explanations": {
            "type": "object",
            "properties": {field: {"type": "string"} for field in _GUIDELINE_FIELDS},
            "required": list(_GUIDELINE_FIELDS),
            "additionalProperties": False,
        },
    },
    "required": list(_GUIDELINE_FIELDS) + ["explanations"],
    "additionalProperties": False,
}
_IDEAS_SCHEMA = {
    "type": "object",
    "properties": {
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content_type": {"type": "string", "enum": list(CONTENT_TYPES)},
                    "brief": {"type": "string"},
                    "image_concept": {"type": "string"},
                    "why_it_helps": {"type": "string"},
                    "suggested_platforms": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(PLATFORMS)},
                    },
                },
                "required": [
                    "title",
                    "content_type",
                    "brief",
                    "image_concept",
                    "why_it_helps",
                    "suggested_platforms",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ideas"],
    "additionalProperties": False,
}
_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "scene_prompt": {"type": "string"},
        "headline": {"type": "string"},
        "subline": {"type": "string"},
        "captions": {"type": "array", "items": _CAPTION_ITEM_SCHEMA},
    },
    "required": ["scene_prompt", "headline", "subline", "captions"],
    "additionalProperties": False,
}
_REPAIR_SCHEMA = {
    "type": "object",
    "properties": {"captions": {"type": "array", "items": _CAPTION_ITEM_SCHEMA}},
    "required": ["captions"],
    "additionalProperties": False,
}
_FACTS_RULE = (
    "Only use prices, percentages, dates, phone numbers and links that appear in the "
    "facts, brief or business profile above. Never invent them."
)


def _log_section(title: str) -> None:
    logger.info(f"social_content: {'=' * 60}")
    logger.info(f"social_content: {title}")
    logger.info(f"social_content: {'=' * 60}")


def _empty_to_none(value) -> str | None:
    text = str(value or "").strip()
    return text or None


# --------------------------------------------------------------------------- #
# Guidelines suggestion
# --------------------------------------------------------------------------- #


def parse_suggested_guidelines(
    raw: dict, guideline_id: str | None
) -> tuple[SocialContentGuidelines, dict[str, str], list[str]]:
    """Validate each suggested field on its own so one bad field is dropped, not the whole suggestion."""
    kept: dict = {"guideline_id": guideline_id}
    dropped: list[str] = []
    for field in _GUIDELINE_FIELDS:
        value = raw.get(field)
        if isinstance(value, str):
            value = _empty_to_none(value)
        if value is None:
            continue
        try:
            checked = SocialContentGuidelines.model_validate({field: value})
        except ValidationError as exc:
            dropped.append(field)
            logger.warning(
                f"social_content: suggested guideline field dropped — field='{field}', error={exc.errors()[0].get('msg')}"
            )
            continue
        kept[field] = getattr(checked, field)

    explanations_raw = raw.get("explanations")
    explanations = {
        field: str(text).strip()
        for field, text in (explanations_raw.items() if isinstance(explanations_raw, dict) else [])
        if field in kept and str(text or "").strip()
    }
    return SocialContentGuidelines.model_validate(kept), explanations, dropped


def _guidelines_prompt(payload: GuidelineSuggestRequest, card_text: str) -> str:
    current = (
        payload.current_guidelines.model_dump_json(exclude_none=True)
        if payload.current_guidelines
        else None
    )
    task = (
        "Improve the current guidelines below: keep what already fits, change only what would help."
        if current
        else "Suggest brand guidelines for this business's social media posts."
    )
    return f"""{task}

Business:
{card_text}

Current guidelines: {user_input(current) if current else "(none)"}

Rules:
- The owner is not a marketing expert: keep every value short and plain.
- writing_style is 1 to 3 words.
- hashtags.max_count is usually 3 to 5.
- restricted_words: only words that genuinely do not fit this business; an empty list is fine.
- required_terms: only terms this business would want in every post (often its name); an empty list is fine.
- Use empty strings or empty lists where you have nothing useful to suggest.
- explanations: one plain sentence per field saying why you suggest it.
"""


def suggest_guidelines(payload: GuidelineSuggestRequest) -> dict:
    business = payload.business
    _log_section("Social content guidelines suggest")
    logger.info(
        "social_content: guidelines suggest — "
        f"business='{business.business_name}', business_id='{business.business_id}', "
        f"has_current={payload.current_guidelines is not None}"
    )

    card = build_context_card(business, payload.current_guidelines, include_reviews=False)
    generation = generate_json(
        prompt=_guidelines_prompt(payload, card["text"]),
        schema=_GUIDELINES_SCHEMA,
        task="social_content_guidelines",
    )
    guideline_id = payload.current_guidelines.guideline_id if payload.current_guidelines else None
    guidelines, explanations, dropped = parse_suggested_guidelines(generation["data"], guideline_id)

    warnings = [f"The suggested '{field}' was not usable and was left out" for field in dropped]
    logger.info(
        "social_content: guidelines suggest completed — "
        f"fields={len(guidelines.model_dump(exclude_none=True, exclude_defaults=True))}, "
        f"dropped={dropped}, model='{generation['model']}'"
    )
    return {
        "status": "success",
        "business_name": business.business_name,
        "business_id": business.business_id,
        "suggested_guidelines": guidelines.model_dump(),
        "explanations": explanations,
        "provider_used": generation["provider"],
        "model": generation["model"],
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# Ideas
# --------------------------------------------------------------------------- #


def normalise_ideas(raw: dict, required_type: str | None) -> tuple[list[Idea], list[str]]:
    """Keep valid ideas (and only the requested type when one was asked for), at most IDEAS_COUNT."""
    items = raw.get("ideas") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return [], ["The AI returned no ideas list"]

    ideas: list[Idea] = []
    warnings: list[str] = []
    for index, item in enumerate(items):
        if len(ideas) >= IDEAS_COUNT:
            break
        if not isinstance(item, dict):
            warnings.append(f"Idea {index + 1} was not an object and was dropped")
            continue
        try:
            idea = Idea.model_validate(
                {
                    **item,
                    "idea_id": uuid.uuid4().hex[:12],
                    "why_it_helps": _empty_to_none(item.get("why_it_helps")),
                }
            )
        except ValidationError as exc:
            warnings.append(f"Idea {index + 1} was incomplete and was dropped")
            logger.warning(
                f"social_content: idea dropped — index={index}, error={exc.errors()[0].get('msg')}"
            )
            continue
        if required_type and idea.content_type != required_type:
            warnings.append(f"Idea {index + 1} was not a {required_type} idea and was dropped")
            continue
        ideas.append(idea)
    return ideas, warnings


def _ideas_prompt(payload: IdeasRequest, card_text: str) -> str:
    month = datetime.now(timezone.utc).strftime("%B")
    if payload.content_type:
        type_rule = f"All {IDEAS_COUNT} ideas must be '{payload.content_type}' posts: {CONTENT_TYPE_RECIPES[payload.content_type]}"
    else:
        recipes = "\n".join(f"  - {name}: {recipe}" for name, recipe in CONTENT_TYPE_RECIPES.items())
        type_rule = f"Spread the {IDEAS_COUNT} ideas across different content types:\n{recipes}"
    return f"""Suggest exactly {IDEAS_COUNT} social media post ideas for this business.

Business:
{card_text}

Platforms the owner posts to: {', '.join(payload.target_platforms)}
Current month: {month}
Facts from the owner: {user_input(payload.facts) if payload.facts else "(none)"}

Rules:
- {type_rule}
- Each idea: a short title, a one-line brief of what the post says, an image concept
  describing what the picture shows (no text in the image), and one plain sentence on
  why it helps the business.
- Offer and event ideas need facts from the owner; without them, suggest other types.
- {_FACTS_RULE}
- suggested_platforms: only from the owner's platforms.
"""


def suggest_ideas(payload: IdeasRequest) -> dict:
    business = payload.business
    _log_section("Social content ideas")
    logger.info(
        "social_content: ideas — "
        f"business='{business.business_name}', business_id='{business.business_id}', "
        f"content_type='{payload.content_type}', platforms={payload.target_platforms}, "
        f"has_facts={bool(payload.facts)}"
    )

    card = build_context_card(business, payload.guidelines, include_reviews=True)
    generation = generate_json(
        prompt=_ideas_prompt(payload, card["text"]),
        schema=_IDEAS_SCHEMA,
        task="social_content_ideas",
    )
    ideas, warnings = normalise_ideas(generation["data"], payload.content_type)

    allowed = set(payload.target_platforms)
    for idea in ideas:
        idea.suggested_platforms = [p for p in idea.suggested_platforms if p in allowed]

    status = "success" if len(ideas) == IDEAS_COUNT else "partial"
    log = logger.info if status == "success" else logger.warning
    log(
        "social_content: ideas completed — "
        f"status={status}, ideas={len(ideas)}/{IDEAS_COUNT}, warnings={len(warnings)}, "
        f"model='{generation['model']}'"
    )
    return {
        "status": status,
        "business_name": business.business_name,
        "business_id": business.business_id,
        "ideas": [idea.model_dump() for idea in ideas],
        "provider_used": generation["provider"],
        "model": generation["model"],
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# Generate
# --------------------------------------------------------------------------- #


def build_image_prompt(
    scene_prompt: str,
    overlay_areas: list[str],
    reference_use: list[str],
    reference_note: str | None,
) -> str:
    """Scene from the plan call plus fixed clauses written in code, not left to the model."""
    lines = [scene_prompt.strip(), NO_TEXT_INSTRUCTION]
    for area in overlay_areas:
        lines.append(
            f"Keep the {OVERLAY_AREA_NAMES[area]} of the frame visually simple "
            "(plain background, sky, wall or soft blur) so text can be placed there."
        )
    for use in reference_use:
        lines.append(REFERENCE_USE_INSTRUCTIONS[use])
    if reference_use and reference_note:
        lines.append(f'User note about the reference image: "{reference_note}"')
    return "\n".join(lines)


def _platform_rules(platforms: list[str], guidelines: SocialContentGuidelines | None) -> str:
    lines = []
    for platform in platforms:
        spec = PLATFORM_SPECS[platform]
        limit = f"{X_TARGET_CHARS} characters" if platform == "x" else f"{spec['max_chars']} characters"
        cap = hashtag_cap(platform, guidelines)
        title = f", title up to {spec['title_max_chars']} characters" if spec["title_max_chars"] else ", title empty"
        hashtags = f"at most {cap} hashtags" if cap else "no hashtags"
        lines.append(f"- {platform}: caption plus hashtags up to {limit}{title}, {hashtags}")
    return "\n".join(lines)


def _plan_prompt(
    payload: GenerateRequest,
    card_text: str,
    content_type: str,
    brief: str,
    image_concept: str | None,
    candidates: list[str],
) -> str:
    reference = (
        f"A reference photo is attached to the image call. Take from it: {', '.join(payload.reference_use)}."
        + (f" Note: {user_input(payload.reference_note)}" if payload.reference_note else "")
        if payload.reference_image_url
        else "No reference photo."
    )
    overlay = (
        f"The frontend will place a logo and text over these areas: {', '.join(payload.overlay_areas)}."
        if payload.overlay_areas
        else "No overlay areas were given."
    )
    return f"""Plan one social media image post for this business.

Business:
{card_text}

Content type: {content_type} ({CONTENT_TYPE_RECIPES[content_type]})
Brief: {user_input(brief)}
Image concept: {user_input(image_concept) if image_concept else "(write one from the brief)"}
Facts from the owner: {user_input(payload.facts) if payload.facts else "(none)"}
{reference}
{overlay}

Return:
- scene_prompt: a detailed description for an image model of one photo-quality image
  for this post, in {payload.aspect_ratio} format. Describe the scene only; the image
  must contain no text, letters, numbers or logos.
- headline and subline: short overlay text the frontend can place on the image
  (headline up to 6 words, subline up to 10 words or empty).
- captions: one per platform below. Hashtags go in the hashtags list without '#',
  never inside the text. Prefer these hashtag candidates when they fit: {user_input(', '.join(candidates)) if candidates else "(none)"}
{_platform_rules(payload.target_platforms, payload.guidelines)}

Rules:
- {_FACTS_RULE}
- Follow the brand guidelines in the business section.
- cta is the call to action used in the text, or empty.
"""


def _repair_prompt(failing: dict[str, dict], violations: dict[str, list[dict]], plan_text: str) -> str:
    blocks = []
    for platform, caption in failing.items():
        problems = "\n".join(f"    - {v['rule']}: {v['detail']}" for v in violations[platform])
        blocks.append(
            f"- {platform}:\n    current: {user_input(caption.get('text'))}\n"
            f"    hashtags: {caption.get('hashtags')}\n    problems:\n{problems}"
        )
    return f"""Fix these social media captions. Keep the meaning, fix only the listed problems.

{plan_text}

Captions to fix:
{chr(10).join(blocks)}

Return one caption per platform listed above, following the same platform limits.
"""


def _normalise_caption(item: dict) -> dict:
    raw_tags = item.get("hashtags") if isinstance(item.get("hashtags"), list) else []
    hashtags: list[str] = []
    for tag in raw_tags:
        # A hashtag cannot hold spaces: "Old Mill" would post as "#Old Mill".
        cleaned = "".join(str(tag).lstrip("#").split())
        if cleaned and cleaned.lower() not in {existing.lower() for existing in hashtags}:
            hashtags.append(cleaned)
    return {
        "text": str(item.get("text") or "").strip(),
        "title": _empty_to_none(item.get("title")),
        "hashtags": hashtags,
        "cta": _empty_to_none(item.get("cta")),
    }


def _captions_by_platform(items, platforms: list[str]) -> dict[str, dict]:
    captions: dict[str, dict] = {}
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("platform") in platforms and item["platform"] not in captions:
            captions[item["platform"]] = _normalise_caption(item)
    return captions


def _allowed_fact_text(payload: GenerateRequest, brief: str, image_concept: str | None, title: str | None) -> str:
    business = payload.business
    parts = [
        payload.facts,
        brief,
        image_concept,
        title,
        business.business_name,
        ", ".join(business.services),
        business.location,
        business.description,
    ]
    return "\n".join(part for part in parts if part)


def _base_result(payload: GenerateRequest, job_id: str, idea_used: dict) -> dict:
    return {
        "job_id": job_id,
        "business_name": payload.business.business_name,
        "business_id": payload.business.business_id,
        "media": [],
        "captions": {},
        "suggested_overlay": None,
        "idea": idea_used,
        "provider_requested": payload.provider,
        "provider_used": None,
        "fallback_used": False,
        "ai_generated": True,
        "warnings": [],
        "errors": [],
    }


def _store_result(payload: GenerateRequest, job_id: str, result: dict) -> None:
    prefix = get_social_content_job_prefix(
        payload.business.business_name, payload.business.business_id, job_id
    )
    if not upload_json_to_s3(s3_key=f"{prefix}/result.json", data=result):
        result["warnings"].append("The job result could not be saved to storage")
        logger.warning(f"social_content: result.json not stored — job_id='{job_id}'")


def _fail(payload: GenerateRequest, job_id: str, result: dict, message: str) -> dict:
    result.update({"status": "error", "event": "social_content_failed", "message": message})
    result["errors"].append(message)
    logger.error(f"social_content: generate failed — job_id='{job_id}', message='{message}'")
    _store_result(payload, job_id, result)
    return result


def generate_content(payload: GenerateRequest, job_id: str) -> dict:
    business = payload.business
    _log_section("Social content generate")

    if payload.idea:
        content_type = payload.idea.content_type
        brief = payload.idea.brief
        image_concept = payload.idea.image_concept
        title = payload.idea.title
        idea_used = payload.idea.model_dump()
    else:
        content_type = payload.content_type
        brief = payload.brief
        image_concept = None
        title = None
        idea_used = {"brief": brief, "content_type": content_type}

    result = _base_result(payload, job_id, idea_used)

    if payload.reference_note and not payload.reference_image_url:
        result["warnings"].append("reference_note was ignored because no reference photo was sent")
        logger.warning(f"social_content: reference_note ignored without a reference photo — job_id='{job_id}'")

    reference = None
    if payload.reference_image_url:
        try:
            reference = download_reference_image(str(payload.reference_image_url))
        except ValueError as exc:
            return _fail(payload, job_id, result, str(exc))

    card = build_context_card(business, payload.guidelines, include_reviews=True)
    candidates = build_hashtag_candidates(
        card["keywords"], business.location, business.brand_hashtags, payload.guidelines
    )
    plan_text = _plan_prompt(payload, card["text"], content_type, brief, image_concept, candidates)

    try:
        plan = generate_json(
            prompt=plan_text,
            schema=_PLAN_SCHEMA,
            task="social_content_plan",
        )
    except RuntimeError as exc:
        return _fail(payload, job_id, result, str(exc))
    if plan["fallback_used"]:
        result["warnings"].append(f"Captions were written by the fallback text model {plan['model']}")

    scene_prompt = str(plan["data"].get("scene_prompt") or "").strip()
    if not scene_prompt:
        return _fail(payload, job_id, result, "The AI did not return an image description. Please try again.")

    image_prompt = build_image_prompt(
        scene_prompt, payload.overlay_areas, payload.reference_use, payload.reference_note
    )
    logger.info(
        f"social_content: image prompt built — job_id='{job_id}', chars={len(image_prompt)}, "
        f"overlay_areas={payload.overlay_areas}, reference_use={payload.reference_use}"
    )

    image = generate_image(
        prompt=image_prompt,
        provider=payload.provider,
        quality=payload.quality,
        aspect_ratio=payload.aspect_ratio,
        reference=reference,
    )
    if image["status"] != "success":
        result["errors"].extend(
            f"{attempt['provider']} {attempt['model']}: {attempt.get('error')}" for attempt in image["attempts"]
        )
        message = (
            "The image could not be created for this request. Try rewording the idea."
            if image["safety_blocked"]
            else "The image could not be created right now. Please try again shortly."
        )
        return _fail(payload, job_id, result, message)

    extension = _IMAGE_EXTENSIONS[image["mime_type"]]
    prefix = get_social_content_job_prefix(business.business_name, business.business_id, job_id)
    s3_key = f"{prefix}/media/image_1.{extension}"
    if not upload_bytes_to_s3(s3_key=s3_key, data=image["data"], content_type=image["mime_type"]):
        return _fail(payload, job_id, result, "The image was created but could not be saved. Please try again.")
    logger.info(
        f"social_content: image stored — job_id='{job_id}', key='{s3_key}', bytes={len(image['data'])}, "
        f"width={image['width']}, height={image['height']}"
    )

    url = generate_presigned_get_url(s3_key=s3_key, expires_in=PRESIGNED_URL_SECONDS)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=PRESIGNED_URL_SECONDS)
    if url is None:
        result["warnings"].append("A download link could not be created; use s3_key")

    result["provider_used"] = image["provider"]
    result["fallback_used"] = image["fallback_used"]
    result["media"].append(
        {
            "type": "image",
            "s3_key": s3_key,
            "url": url,
            "url_expires_at": expires_at.isoformat(timespec="seconds") if url else None,
            "aspect_ratio": payload.aspect_ratio,
            "width": image["width"],
            "height": image["height"],
            "format": extension,
            "provider": image["provider"],
            "model": image["model"],
            "status": f"⚠️ Fallback to {image['provider']}" if image["fallback_used"] else "✅ Success",
        }
    )
    if image["fallback_used"]:
        result["warnings"].append(
            f"The image was made with {image['provider']} because {payload.provider} failed"
        )

    # Captions: check in code, one repair call for failing platforms, report what is left.
    allowed_text = _allowed_fact_text(payload, brief, image_concept, title)
    captions = _captions_by_platform(plan["data"].get("captions"), payload.target_platforms)
    violations: dict[str, list[dict]] = {}
    for platform in payload.target_platforms:
        if platform not in captions:
            captions[platform] = {"text": "", "title": None, "hashtags": [], "cta": None}
            violations[platform] = [{"rule": "missing", "detail": "No caption was written for this platform"}]
        else:
            violations[platform] = validate_caption(platform, captions[platform], payload.guidelines, allowed_text)
    failing = {platform: captions[platform] for platform, found in violations.items() if found}
    logger.info(
        f"social_content [caption]: checked — job_id='{job_id}', "
        f"violations_before={ {p: len(v) for p, v in violations.items() if v} }"
    )

    if failing:
        try:
            repair = generate_json(
                prompt=_repair_prompt(failing, violations, plan_text),
                schema=_REPAIR_SCHEMA,
                task="social_content_caption_repair",
            )
            repaired = _captions_by_platform(repair["data"].get("captions"), list(failing))
            for platform, caption in repaired.items():
                captions[platform] = caption
                violations[platform] = validate_caption(platform, caption, payload.guidelines, allowed_text)
        except RuntimeError as exc:
            result["warnings"].append("Caption repair was not available; the listed caption warnings remain")
            logger.warning(f"social_content [caption]: repair call failed — job_id='{job_id}', error={exc}")
        logger.info(
            f"social_content [caption]: after repair — job_id='{job_id}', "
            f"violations_after={ {p: len(v) for p, v in violations.items() if v} }"
        )

    result["captions"] = {
        platform: {**captions[platform], "warnings": violations[platform]}
        for platform in payload.target_platforms
    }

    headline = _empty_to_none(plan["data"].get("headline"))
    subline = _empty_to_none(plan["data"].get("subline"))
    if headline:
        overlay_issues = check_invented_facts(f"{headline}\n{subline or ''}", allowed_text)
        if overlay_issues:
            result["warnings"].append(
                "The suggested overlay was left out because it used facts that were not given"
            )
            logger.warning(
                f"social_content: overlay dropped — job_id='{job_id}', issues={overlay_issues}"
            )
        else:
            result["suggested_overlay"] = {"headline": headline, "subline": subline}

    remaining = sum(len(v) for v in violations.values())
    status = "partial" if remaining or image["fallback_used"] or url is None else "success"
    result.update({"status": status, "event": "social_content_generated"})
    _store_result(payload, job_id, result)

    log = logger.info if status == "success" else logger.warning
    log(
        "social_content: generate completed — "
        f"job_id='{job_id}', status={status}, provider_used='{image['provider']}', "
        f"fallback_used={image['fallback_used']}, caption_warnings={remaining}, "
        f"warnings={len(result['warnings'])}"
    )
    return result
