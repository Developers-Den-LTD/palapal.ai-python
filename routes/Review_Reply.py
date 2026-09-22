from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from schema.review_reply_schema import EditDraftRequest, ReviewReplyRequest
from services.logger_services import logger
from services.review_reply_services import edit_review_drafts, generate_review_replies

router = APIRouter(tags=["Review Reply"], prefix="/api")


def run_review_reply(payload: ReviewReplyRequest):
    try:
        generate_review_replies(payload)
        logger.info("Background review reply generation completed.")
    except Exception as exc:
        logger.exception(f"Background review reply generation failed: {exc}")


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
        f"comment_count={len(payload.comments)}"
    )

    background_tasks.add_task(run_review_reply, payload)

    return {
        "message": "Review reply generation has started. Please refresh the page in a few moments to view the results."
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
