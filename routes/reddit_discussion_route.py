from fastapi import APIRouter, HTTPException, status

from schema.reddit_discussion_schema import RedditDiscussionRequest
from services.logger_services import logger
from services.reddit_discussion_services import analyze_reddit_discussion

router = APIRouter(
    tags=["Reddit Discussion"],
    prefix="/api",
)


@router.post(
    "/reddit-discussion",
    status_code=status.HTTP_200_OK,
)
def reddit_discussion(payload: RedditDiscussionRequest):
    logger.info(
        "reddit_discussion route: POST /api/reddit-discussion — "
        f"business='{payload.business_name}', loc='{payload.business_loc}'"
    )
    try:
        result = analyze_reddit_discussion(payload)
        if result.get("found"):
            logger.info(
                "reddit_discussion route: found discussion — "
                f"business='{result.get('business_name')}', "
                f"sources={len(result.get('sources') or [])}, "
                f"model='{result.get('model_used')}'"
            )
        else:
            logger.info(
                "reddit_discussion route: no Reddit discussion found — "
                f"business='{payload.business_name}', loc='{payload.business_loc}'"
            )
        return result
    except Exception as exc:
        logger.exception(f"reddit_discussion route: request failed — {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Reddit discussion analysis failed: {str(exc)}",
        )
