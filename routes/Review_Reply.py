from fastapi import APIRouter, HTTPException, status, BackgroundTasks

from schema.review_reply_schema import ReviewReplyRequest
from services.logger_services import logger
from services.review_reply_services import generate_review_replies

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
        "message": "Review reply generation has started. Please allow 1–2 minutes for the process to complete."
    }