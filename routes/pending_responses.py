from fastapi import APIRouter, Depends, HTTPException, status

from schema.pending_responses_schema import PendingResponsesRequest
from services.logger_services import logger
from services.pending_responses_services import get_pending_responses

router = APIRouter(
    tags=["Pending Responses"],
    prefix="/api",
)


@router.post(
    "/pending-responses",
    status_code=status.HTTP_200_OK
)
def pending_responses(payload: PendingResponsesRequest):
    logger.info(
        "pending_responses route: POST /api/pending-responses — "
        f"business='{payload.business_name}'"
    )
    try:
        result = get_pending_responses(payload.business_name)
        if result["status"] == "success":
            logger.info(
                "pending_responses route: request completed successfully — "
                f"business='{result.get('business')}', "
                f"total_pending={result['summary']['total_pending']}, "
                f"google_maps={result['summary']['google_maps']}, "
                f"yelp={result['summary']['yelp']}, "
                f"tripadvisor={result['summary']['tripadvisor']}"
            )
        else:
            logger.warning(
                f"pending_responses route: request completed with error — "
                f"message={result.get('message')}"
            )
        return result
    except Exception as e:
        logger.exception(f"pending_responses route: request failed — {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pending responses lookup failed: {str(e)}",
        )
