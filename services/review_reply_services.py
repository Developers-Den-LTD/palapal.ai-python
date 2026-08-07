import json
from datetime import datetime

from openai import OpenAI

from core.config import settings
from schema.review_reply_schema import ReplyTemplate, ReviewComment, ReviewReplyRequest
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
            f"with business_id={business_id}. Run scrape API first."
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


def _build_prompt(
    business_name: str,
    comment_items: list[dict],
    template: ReplyTemplate | None = None,
) -> str:
    if template:
        style_instructions = f"""
Template instructions:
- Write every reply in a {template.tone.strip()} tone
- Follow this style and messaging approach: {template.prompt.strip()}
- Adapt the wording to each specific review while keeping the same tone and style
"""
    else:
        style_instructions = """
Guidelines:
- Sound like a real business owner: warm, respectful, and genuine
- Thank the customer for their feedback
- For positive reviews (4-5 stars): express gratitude and invite them back
- For mixed reviews (3 stars): acknowledge feedback and mention improvement
- For negative reviews (1-2 stars): apologize sincerely, stay calm, and offer to make things right offline when appropriate
- If rating is missing, infer tone from the comment text
"""

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
- Keep each reply concise: 2-4 sentences
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
) -> None:
    replies_by_uuid = {item["uuid"]: item["reply"] for item in replies}

    for comment_item in comment_items:
        comment_uuid = comment_item["uuid"]
        platform = comment_item["platform"]
        index = comment_item["index"]
        scraped_data[platform]["reviews"][index]["AI_Draft"] = replies_by_uuid[
            comment_uuid
        ]


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


def generate_review_replies(payload: ReviewReplyRequest) -> dict:
    business_name = payload.business_name.strip()
    business_id = _validate_business_id(payload.business_id)
    scraped_data = _load_scraped_data(business_name, business_id)
    comment_items = _prepare_comments(
        payload.comments,
        payload.date,
        scraped_data,
    )

    logger.info(
        f"review_reply: generating replies — business='{business_name}', "
        f"business_id='{business_id}', comment_count={len(comment_items)}, "
        f"batch_size={BATCH_SIZE}, "
        f"template={'yes' if payload.template else 'no'}"
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

    _apply_replies_to_scraped_data(scraped_data, comment_items, current_replies)
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
