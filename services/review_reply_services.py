import json
from datetime import datetime

from openai import OpenAI

from core.config import settings
from schema.review_reply_schema import (
    CurrentTemplateInput,
    EditDraftRequest,
    ReplyTemplate,
    ReviewComment,
    ReviewReplyRequest,
    SuggestTemplateRequest,
    SuggestedTemplate,
    SUGGESTED_TONES,
)
from services.logger_services import logger
from services.s3_service import load_scraped_result_data
from services.scrapper_services import save_scraped_result
from utils.scraped_result_paths import get_scraped_result_path

LLM_MODEL = "gpt-5.4-nano"
TIMESTAMP_FORMAT = "%d %B %Y %H:%M"
BATCH_SIZE = 10
MAX_BATCH_ATTEMPTS = 2
PLATFORMS = ("google_maps", "yelp", "tripadvisor")


def _now() -> str:
    return datetime.now().strftime(TIMESTAMP_FORMAT)


def _validate_business_id(business_id: str | int | None) -> str | int:
    if business_id is None:
        raise ValueError("business_id is null")
    if isinstance(business_id, str) and not business_id.strip():
        raise ValueError("business_id is null")
    return business_id


def _validate_uuid(comment_uuid: str | None) -> str:
    if comment_uuid is None or not str(comment_uuid).strip():
        raise ValueError("uuid is null")
    return str(comment_uuid).strip()


def _resolve_request_date(request_date: str | None) -> str | None:
    if request_date and request_date.strip():
        return request_date.strip()
    return None


def _resolve_comment_date(
    comment: ReviewComment,
    request_date: str | None,
    stored_date: str | None = None,
) -> str | None:
    if comment.date and comment.date.strip():
        return comment.date.strip()
    return _resolve_request_date(request_date) or (
        stored_date.strip() if stored_date and str(stored_date).strip() else None
    )


def _get_review_uuid(review: dict) -> str:
    return str(review.get("UUID") or review.get("uuid") or "").strip()


def _find_review_location(
    scraped_data: dict,
    target_uuid: str,
) -> tuple[str, int] | None:
    normalized_uuid = target_uuid.lower()
    for platform in PLATFORMS:
        reviews = scraped_data.get(platform, {}).get("reviews", [])
        for index, review in enumerate(reviews):
            if _get_review_uuid(review).lower() == normalized_uuid:
                return platform, index
    return None


def _load_scraped_data(business_name: str, business_id: str | int) -> dict:
    try:
        return load_scraped_result_data(business_name, business_id)
    except FileNotFoundError as exc:
        raise ValueError(
            f"No scraped_result.json found for business '{business_name}' "
            f"with business_id='{business_id}'. "
            "Check business_name/business_id, or run scrape API first."
        ) from exc


def _prepare_comments(
    comments: list[ReviewComment],
    request_date: str | None,
    scraped_data: dict,
) -> list[dict]:
    prepared: list[dict] = []

    for comment in comments:
        if not comment.comment or not comment.comment.strip():
            raise ValueError("comment is required to generate a reply")

        comment_uuid = _validate_uuid(comment.uuid)
        location = _find_review_location(scraped_data, comment_uuid)
        if not location:
            raise ValueError(
                f"uuid '{comment_uuid}' not found in scraped_result.json"
            )

        platform, index = location
        existing_review = scraped_data[platform]["reviews"][index]
        previous_reply = existing_review.get("AI_Draft")
        has_previous_draft = bool(
            previous_reply is not None and str(previous_reply).strip()
        )

        prepared.append(
            {
                "uuid": comment_uuid,
                "comment": comment.comment.strip(),
                "rating": comment.rating,
                "author": comment.author,
                "date": _resolve_comment_date(
                    comment,
                    request_date,
                    existing_review.get("date"),
                ),
                "is_update": has_previous_draft,
                "previous_reply": previous_reply if has_previous_draft else None,
                "platform": platform,
                "index": index,
            }
        )

    return prepared


def _build_template_style_instructions(template: ReplyTemplate) -> str:
    """Build LLM style block from template fields (skip empty optional ones)."""
    lines = ["Template instructions:"]
    lines.append(f"- Write every reply in a {template.tone} tone")

    if template.writing_style:
        lines.append(f"- Writing style: {template.writing_style}")

    if template.response_length:
        lines.append(f"- Response length: {template.response_length}")

    if template.preferred_wording:
        wording = "; ".join(template.preferred_wording)
        lines.append(
            "- Preferred wording (use when natural, do not force awkwardly): "
            f"{wording}"
        )

    if template.sign_off:
        lines.append(
            f'- Sign-off: end with "{template.sign_off}" when a closing is appropriate'
        )

    if template.response_structure:
        structure = " → ".join(template.response_structure)
        lines.append(
            "- Response structure (follow this order): "
            + "; ".join(
                f"{index}. {part}"
                for index, part in enumerate(template.response_structure, start=1)
            )
            + f" ({structure})"
        )

    lines.append(f"- Follow this style and messaging approach: {template.prompt}")
    lines.append(
        "- Adapt the wording to each specific review while keeping the same "
        "tone, style, and template preferences"
    )
    return "\n" + "\n".join(lines) + "\n"


def _build_prompt(
    business_name: str,
    comment_items: list[dict],
    template: ReplyTemplate | None = None,
) -> str:
    if template:
        style_instructions = _build_template_style_instructions(template)
        length_rule = (
            f"- Keep each reply to this length guidance: {template.response_length}"
            if template.response_length
            else "- Keep each reply concise: 2-4 sentences"
        )
    else:
        style_instructions = """
Guidelines:
- Sound like a real business owner: warm, respectful, and genuine
- Thank the customer for their feedback
- For positive reviews (4-5 stars): express gratitude and invite them back
- For mixed reviews (3 stars): acknowledge feedback and mention improvement
- For negative reviews (1-2 stars): apologize sincerely, stay calm, and offer to make things right offline when appropriate
- If rating is missing or 0, infer tone from the comment text and still write a full reply
"""
        length_rule = "- Keep each reply concise: 2-4 sentences"

    llm_comments = [
        {
            "uuid": item["uuid"],
            "comment": item["comment"],
            "rating": item["rating"],
            "author": item["author"],
            "date": item["date"],
        }
        for item in comment_items
    ]

    regenerate_notes = []
    for item in comment_items:
        if item.get("previous_reply"):
            regenerate_notes.append(
                f'- uuid "{item["uuid"]}": write a fresh alternative reply. '
                f'Previous reply was: {item["previous_reply"]}'
            )

    regenerate_section = ""
    if regenerate_notes:
        regenerate_section = (
            "\n\nFor these reviews, write a new alternative reply "
            "(do not copy the previous reply verbatim):\n"
            + "\n".join(regenerate_notes)
        )

    return f"""You are the owner of "{business_name}" writing public replies to customer reviews.

Write a thoughtful, professional owner reply for every review below.
{style_instructions}
- Use the reviewer's name when author is provided (e.g. "Hi Sarah,")
{length_rule}
- Do not invent policies, discounts, or contact details
- Do not mention that you are an AI
- Do not repeat the full review back to the customer

Return only valid JSON in this exact shape:
{{
  "replies": [
    {{
      "uuid": "same-uuid-from-review",
      "reply": "Owner reply text here"
    }}
  ]
}}

Include one reply for every review uuid listed below.
{regenerate_section}

Reviews:
{json.dumps(llm_comments, ensure_ascii=False, indent=2)}"""


def _parse_replies(raw_text: str, comment_items: list[dict]) -> list[dict]:
    payload = json.loads(raw_text)
    raw_replies = payload.get("replies")

    if not isinstance(raw_replies, list):
        raise ValueError("The model response does not contain a replies list")

    replies_by_uuid = {
        str(item.get("uuid", "")).strip().lower(): str(item.get("reply", "")).strip()
        for item in raw_replies
        if isinstance(item, dict)
    }

    results = []
    for comment_item in comment_items:
        comment_uuid = comment_item["uuid"]
        reply = replies_by_uuid.get(comment_uuid.lower(), "")
        if not reply:
            raise ValueError(f"Missing reply for comment uuid={comment_uuid}")

        results.append(
            {
                "uuid": comment_uuid,
                "comment": comment_item["comment"],
                "rating": comment_item["rating"],
                "author": comment_item["author"],
                "date": comment_item["date"],
                "reply": reply,
                "action": "updated" if comment_item["is_update"] else "created",
            }
        )

    return results


def _chunk_items(items: list[dict], size: int) -> list[list[dict]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _generate_batch_replies(
    client: OpenAI,
    business_name: str,
    batch_items: list[dict],
    template: ReplyTemplate | None,
) -> list[dict]:
    temperature = 0.8 if any(item["is_update"] for item in batch_items) else 0.7
    last_error: ValueError | None = None

    for attempt in range(1, MAX_BATCH_ATTEMPTS + 1):
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You write short, professional public review replies on behalf "
                        "of business owners. Return only valid JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": _build_prompt(
                        business_name,
                        batch_items,
                        template,
                    ),
                },
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
            max_completion_tokens=4096,
        )

        raw_text = response.choices[0].message.content or ""
        try:
            return _parse_replies(raw_text, batch_items)
        except ValueError as exc:
            last_error = exc
            logger.warning(
                f"review_reply: batch generation attempt {attempt}/{MAX_BATCH_ATTEMPTS} "
                f"failed — {exc}"
            )

    raise last_error or ValueError("Failed to generate replies for batch")


def _apply_replies_to_scraped_data(
    scraped_data: dict,
    comment_items: list[dict],
    replies: list[dict],
    template_id: str | None = None,
) -> None:
    replies_by_uuid = {item["uuid"]: item["reply"] for item in replies}

    for comment_item in comment_items:
        comment_uuid = comment_item["uuid"]
        platform = comment_item["platform"]
        index = comment_item["index"]
        scraped_data[platform]["reviews"][index]["AI_Draft"] = replies_by_uuid[
            comment_uuid
        ]
        if template_id is not None:
            scraped_data[platform]["reviews"][index]["template_id"] = template_id


def _persist_scraped_data(
    scraped_data: dict,
    business_name: str,
    business_id: str | int,
) -> dict:
    local_path = get_scraped_result_path(business_name, business_id)
    local_saved = False
    s3_saved = False

    try:
        save_scraped_result(scraped_data, business_name, business_id)
        local_saved = local_path.exists()
        s3_saved = local_saved
        logger.info(
            f"review_reply: saved AI_Draft to scraped_result.json — {local_path}"
        )
    except Exception as exc:
        logger.error(f"review_reply: failed to save scraped_result.json — {exc}")

    return {
        "local_saved": local_saved,
        "s3_saved": s3_saved,
    }


def _normalize_business_id(value: str | int | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _assert_business_id_matches(
    scraped_data: dict,
    business_id: str | int,
) -> None:
    """
    Ensure request business_id matches the id stored in scraped_result.json
    when that field is present.
    """
    request_id = _normalize_business_id(business_id)
    if not request_id:
        raise ValueError("business_id is required")

    stored_raw = scraped_data.get("business_id")
    stored_id = _normalize_business_id(stored_raw)
    if stored_id is None:
        # Older files may omit business_id; path already scoped by business_id.
        return

    if stored_id.lower() != request_id.lower():
        raise ValueError(
            f"business_id mismatch: request has '{request_id}' but "
            f"scraped_result.json has '{stored_id}'"
        )


def edit_review_drafts(payload: EditDraftRequest) -> dict:
    """
    Save edit_draft onto matching reviews by uuid (writes to stored AI_Draft).
    Other review fields are left unchanged.
    """
    business_name = payload.business_name.strip()
    business_id = _validate_business_id(payload.business_id)

    scraped_data = _load_scraped_data(business_name, business_id)
    _assert_business_id_matches(scraped_data, business_id)

    seen_uuids: set[str] = set()
    updates: list[dict] = []

    for item in payload.comments:
        comment_uuid = _validate_uuid(item.uuid)
        uuid_key = comment_uuid.lower()
        if uuid_key in seen_uuids:
            raise ValueError(f"duplicate uuid in request: '{comment_uuid}'")
        seen_uuids.add(uuid_key)

        location = _find_review_location(scraped_data, comment_uuid)
        if not location:
            raise ValueError(
                f"uuid '{comment_uuid}' not found in scraped_result.json "
                f"for business_id='{business_id}'"
            )

        try:
            new_draft = item.resolved_edit_draft()
        except ValueError as exc:
            raise ValueError(f"uuid '{comment_uuid}': {exc}") from exc

        platform, index = location
        previous_draft = scraped_data[platform]["reviews"][index].get("AI_Draft")

        scraped_data[platform]["reviews"][index]["AI_Draft"] = new_draft

        updates.append(
            {
                "uuid": comment_uuid,
                "platform": platform,
                "previous_AI_Draft": previous_draft,
                "edit_draft": new_draft,
                "AI_Draft": new_draft,
                "action": "updated",
            }
        )

    logger.info(
        f"review_reply edit_draft: saving — business='{business_name}', "
        f"business_id='{business_id}', updates={len(updates)}"
    )

    storage = _persist_scraped_data(scraped_data, business_name, business_id)
    if not storage.get("local_saved"):
        raise RuntimeError(
            "Failed to save updated AI_Draft to scraped_result.json"
        )

    return {
        "status": "success",
        "message": "AI_Draft updated successfully from edit_draft",
        "business_name": business_name,
        "business_id": business_id,
        "updated_at": _now(),
        "updated_count": len(updates),
        "updates": updates,
        "storage": storage,
    }


def generate_review_replies(payload: ReviewReplyRequest) -> dict:
    business_name = payload.business_name.strip()
    business_id = _validate_business_id(payload.business_id)
    scraped_data = _load_scraped_data(business_name, business_id)
    comment_items = _prepare_comments(
        payload.comments,
        payload.date,
        scraped_data,
    )

    template_id = (
        payload.template.template_id.strip()
        if payload.template is not None
        else None
    )

    logger.info(
        f"review_reply: generating replies — business='{business_name}', "
        f"business_id='{business_id}', comment_count={len(comment_items)}, "
        f"batch_size={BATCH_SIZE}, "
        f"template={'yes' if payload.template else 'no'}, "
        f"template_id='{template_id}'"
    )

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    batches = _chunk_items(comment_items, BATCH_SIZE)
    current_replies: list[dict] = []

    for batch_index, batch_items in enumerate(batches, start=1):
        logger.info(
            f"review_reply: processing batch {batch_index}/{len(batches)} "
            f"({len(batch_items)} comments)"
        )
        batch_replies = _generate_batch_replies(
            client,
            business_name,
            batch_items,
            payload.template,
        )
        current_replies.extend(batch_replies)
        logger.info(
            f"review_reply: batch {batch_index}/{len(batches)} completed "
            f"({len(batch_replies)} replies)"
        )

    _apply_replies_to_scraped_data(
        scraped_data,
        comment_items,
        current_replies,
        template_id=template_id,
    )
    storage = _persist_scraped_data(scraped_data, business_name, business_id)

    logger.info(
        f"review_reply: completed — business='{business_name}', "
        f"processed={len(current_replies)}, "
        f"local_saved={storage['local_saved']}, "
        f"s3_saved={storage['s3_saved']}"
    )

    return {
        "status": "success",
        "business_name": business_name,
        "business_id": business_id,
        "generated_at": _now(),
        "replies": current_replies,
        "storage": storage,
    }


SUGGEST_TEMPLATE_MESSAGE = (
    "You have modified the AI-generated response. "
    "Would you like to update your response template to reflect these preferences?"
)
MAX_SUGGEST_ATTEMPTS = 2


def _normalize_draft_text(text: str | None) -> str:
    return " ".join((text or "").split()).strip().lower()


def _drafts_meaningfully_differ(original: str, edited: str) -> bool:
    """True when edited text is meaningfully different from the original AI draft."""
    original_norm = _normalize_draft_text(original)
    edited_norm = _normalize_draft_text(edited)
    if not edited_norm:
        return False
    if not original_norm:
        # User wrote a reply with no prior AI draft — treat as a preference signal.
        return True
    return original_norm != edited_norm


def _current_template_as_dict(
    template: CurrentTemplateInput | None,
) -> dict | None:
    if template is None:
        return None
    data = template.model_dump(by_alias=True, exclude_none=True)
    return data or None


def _build_suggest_template_prompt(payload: SuggestTemplateRequest) -> str:
    current = _current_template_as_dict(payload.current_template)
    review_context = {
        "author": payload.author,
        "rating": payload.rating,
        "comment": payload.comment,
        "uuid": payload.uuid,
    }
    return f"""You analyze how a business owner edited an AI-generated public review reply,
then propose an updated response template that reflects their preferences.

Business name: {payload.business_name}

Original AI-generated reply:
{payload.original_AI_Draft}

User-edited reply:
{payload.edit_draft}

Current template (may be null):
{json.dumps(current, ensure_ascii=False, indent=2)}

Review context (optional):
{json.dumps(review_context, ensure_ascii=False, indent=2)}

Infer preferences from the differences between the original and edited replies.
Focus on:
- tone
- writing style
- response length
- preferred wording / phrases
- sign-off
- response structure
- a reusable Prompt for future AI replies

Rules:
- tone MUST be exactly one of: {", ".join(SUGGESTED_TONES)}
- preferred_wording must be an array of short phrases (or empty array)
- response_structure must be an array of ordered steps (e.g. ["greeting", "thanks", "invite back", "sign-off"])
- Prompt must be clear reusable instructions for generating future replies
- Do not invent business policies, discounts, or contact details
- Keep suggestions practical and based on the edit

Return only valid JSON in this exact shape:
{{
  "tone": "Friendly",
  "writing_style": "...",
  "response_length": "...",
  "preferred_wording": ["..."],
  "sign_off": "...",
  "response_structure": ["greeting", "thanks", "invite back", "sign-off"],
  "Prompt": "...",
  "title": "optional label",
  "diff_summary": "short summary of what the user changed"
}}"""


def _parse_suggested_template(raw_text: str) -> tuple[SuggestedTemplate, str | None]:
    payload = json.loads(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("Model response must be a JSON object")

    suggested = SuggestedTemplate.model_validate(
        {
            "template_id": payload.get("template_id") or payload.get("templateId"),
            "title": payload.get("title"),
            "tone": payload.get("tone"),
            "writing_style": payload.get("writing_style"),
            "response_length": payload.get("response_length"),
            "preferred_wording": payload.get("preferred_wording"),
            "sign_off": payload.get("sign_off") or payload.get("sign_offs"),
            "response_structure": payload.get("response_structure"),
            "Prompt": payload.get("Prompt") or payload.get("prompt"),
        }
    )
    diff_summary = payload.get("diff_summary")
    if diff_summary is not None:
        diff_summary = str(diff_summary).strip() or None
    return suggested, diff_summary


def _analyze_template_with_llm(
    client: OpenAI,
    payload: SuggestTemplateRequest,
) -> tuple[SuggestedTemplate, str | None]:
    last_error: Exception | None = None

    for attempt in range(1, MAX_SUGGEST_ATTEMPTS + 1):
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert at reverse-engineering writing preferences "
                        "from edited review replies. Return only valid JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": _build_suggest_template_prompt(payload),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.4,
            max_completion_tokens=2048,
        )
        raw_text = response.choices[0].message.content or ""
        try:
            return _parse_suggested_template(raw_text)
        except Exception as exc:
            last_error = exc
            logger.warning(
                f"review_reply suggest_template: parse attempt "
                f"{attempt}/{MAX_SUGGEST_ATTEMPTS} failed — {exc}"
            )

    raise ValueError(
        f"Failed to analyze template preferences: {last_error}"
    )


def suggest_template_from_edit(payload: SuggestTemplateRequest) -> dict:
    """
    Analyze original AI draft vs user edit_draft and suggest template updates.
    Does not save anything — Accept/Reject stays with the client.
    """
    business_name = payload.business_name.strip()
    original = payload.original_AI_Draft if payload.original_AI_Draft is not None else ""
    edited = payload.edit_draft if payload.edit_draft is not None else ""

    was_modified = _drafts_meaningfully_differ(original, edited)
    logger.info(
        "review_reply suggest_template: start — "
        f"business='{business_name}', "
        f"business_id='{payload.business_id}', "
        f"was_modified={was_modified}, "
        f"has_current_template={payload.current_template is not None}"
    )

    if not was_modified:
        current_template_payload = (
            payload.current_template.model_dump(by_alias=True, exclude_none=True)
            if payload.current_template is not None
            else None
        )
        return {
            "status": "success",
            "business_name": business_name,
            "business_id": payload.business_id,
            "was_modified": False,
            "should_suggest_update": False,
            "message": (
                "No meaningful changes detected between the AI-generated "
                "response and the edited draft."
            ),
            "current_template": current_template_payload,
            "detected_preferences": None,
            "suggested_template": None,
            "diff_summary": None,
            "analyzed_at": _now(),
        }

    if not edited.strip():
        raise ValueError("edit_draft cannot be empty when suggesting a template update")

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    suggested, diff_summary = _analyze_template_with_llm(client, payload)
    suggested_payload = suggested.model_dump(by_alias=True)

    # Preserve client template identity + title when updating an existing template.
    if payload.current_template is not None:
        if payload.current_template.template_id and not suggested_payload.get(
            "template_id"
        ):
            suggested_payload["template_id"] = payload.current_template.template_id
        if payload.current_template.title and not suggested_payload.get("title"):
            suggested_payload["title"] = payload.current_template.title

    detected_preferences = {
        "tone": suggested_payload.get("tone"),
        "writing_style": suggested_payload.get("writing_style"),
        "response_length": suggested_payload.get("response_length"),
        "preferred_wording": suggested_payload.get("preferred_wording"),
        "sign_off": suggested_payload.get("sign_off"),
        "response_structure": suggested_payload.get("response_structure"),
        "Prompt": suggested_payload.get("Prompt"),
    }

    current_template_payload = (
        payload.current_template.model_dump(by_alias=True, exclude_none=True)
        if payload.current_template is not None
        else None
    )

    logger.info(
        "review_reply suggest_template: completed — "
        f"business='{business_name}', tone='{suggested.tone}', "
        f"template_id='{suggested_payload.get('template_id')}'"
    )

    return {
        "status": "success",
        "business_name": business_name,
        "business_id": payload.business_id,
        "was_modified": True,
        "should_suggest_update": True,
        "message": SUGGEST_TEMPLATE_MESSAGE,
        "current_template": current_template_payload,
        "detected_preferences": detected_preferences,
        "suggested_template": suggested_payload,
        "diff_summary": diff_summary,
        "analyzed_at": _now(),
    }
