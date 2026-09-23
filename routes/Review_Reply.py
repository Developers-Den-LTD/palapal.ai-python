import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from schema.review_reply_schema import (
    EditDraftRequest,
    ReviewReplyRequest,
    SuggestTemplateRequest,
)
from services.logger_services import logger
from services.review_reply_services import (
    edit_review_drafts,
    generate_review_replies,
    suggest_template_from_edit,
)
from services.webhook_poster import post_to_webhook

router = APIRouter(tags=["Review Reply"], prefix="/api")


async def run_review_reply(payload: ReviewReplyRequest):
    webhook_url = str(payload.webhook_url)
    business_name = payload.business_name.strip()
    business_id = payload.business_id

    try:
        result = await asyncio.to_thread(generate_review_replies, payload)
        processed_count = len(result.get("replies") or [])
        storage = result.get("storage") or {}
        logger.info(
            "Background review reply generation completed — "
            f"business='{business_name}', processed={processed_count}, "
            f"local_saved={storage.get('local_saved')}"
        )
        await post_to_webhook(
            webhook_url,
            {
                "status": "success",
                "event": "review_reply_completed",
                "business_name": business_name,
                "business_id": business_id,
                "processed_count": processed_count,
                "message": (
                    "Review reply generation completed and "
                    "scraped_result.json was updated."
                ),
            },
        )
    except Exception as exc:
        logger.exception(f"Background review reply generation failed: {exc}")
        try:
            await post_to_webhook(
                webhook_url,
                {
                    "status": "error",
                    "event": "review_reply_failed",
                    "business_name": business_name,
                    "business_id": business_id,
                    "message": str(exc),
                },
            )
        except Exception as webhook_exc:
            logger.exception(
                f"review_reply: failed to notify webhook after error — {webhook_exc}"
            )


@router.post("/review-reply", status_code=status.HTTP_202_ACCEPTED)
def review_reply(
    payload: ReviewReplyRequest,
    background_tasks: BackgroundTasks,
):
    logger.info(
        "review_reply route: POST /api/review-reply — "
        f"business='{payload.business_name}', "
        f"business_id='{payload.business_id}', "
        f"template={'yes' if payload.template else 'no'}, "
        f"template_id="
        f"'{payload.template.template_id if payload.template else None}', "
        f"comment_count={len(payload.comments)}, "
        f"webhook_url='{payload.webhook_url}'"
    )

    background_tasks.add_task(run_review_reply, payload)

    return {
        "status": "accepted",
        "message": (
            "Review reply generation has started. "
            "A completion indicator will be posted to the webhook_url "
            "when scraped_result.json is updated."
        ),
        "webhook_url": str(payload.webhook_url),
        "business_name": payload.business_name,
        "business_id": payload.business_id,
        "comment_count": len(payload.comments),
    }


@router.post("/review-reply/edit-draft", status_code=status.HTTP_200_OK)
def review_reply_edit_draft(payload: EditDraftRequest):
    logger.info(
        "review_reply route: POST /api/review-reply/edit-draft — "
        f"business='{payload.business_name}', "
        f"business_id='{payload.business_id}', "
        f"comment_count={len(payload.comments)}"
    )
    try:
        result = edit_review_drafts(payload)
        logger.info(
            "review_reply edit_draft route: completed — "
            f"business='{result.get('business_name')}', "
            f"business_id='{result.get('business_id')}', "
            f"updated_count={result.get('updated_count')}"
        )
        return result
    except ValueError as exc:
        logger.warning(f"review_reply edit_draft route: validation error — {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception(f"review_reply edit_draft route: failed — {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save AI_Draft: {exc}",
        ) from exc


@router.post("/review-reply/suggest-template", status_code=status.HTTP_200_OK)
def review_reply_suggest_template(payload: SuggestTemplateRequest):
    logger.info(
        "review_reply route: POST /api/review-reply/suggest-template — "
        f"business='{payload.business_name}', "
        f"business_id='{payload.business_id}', "
        f"has_current_template={payload.current_template is not None}"
    )
    try:
        result = suggest_template_from_edit(payload)
        logger.info(
            "review_reply suggest_template route: completed — "
            f"business='{result.get('business_name')}', "
            f"should_suggest_update={result.get('should_suggest_update')}, "
            f"was_modified={result.get('was_modified')}"
        )
        return result
    except ValueError as exc:
        logger.warning(
            f"review_reply suggest_template route: validation error — {exc}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception(f"review_reply suggest_template route: failed — {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to suggest template update: {exc}",
        ) from exc
