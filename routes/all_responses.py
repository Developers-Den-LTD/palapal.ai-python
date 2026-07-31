from fastapi import APIRouter, Depends, HTTPException, status

from schema.all_responses_schema import AllResponsesRequest
from services.all_responses_services import get_all_responses
from services.logger_services import logger

router = APIRouter(
    tags=["All Responses"],
    prefix="/api",
)


@router.post(
    "/all-responses",
    status_code=status.HTTP_200_OK
)
def all_responses(payload: AllResponsesRequest):
    logger.info(
        "all_responses route: POST /api/all-responses — "
        f"business='{payload.business_name}', business_id='{payload.business_id}'"
    )
    try:
        result = get_all_responses(payload.business_name, payload.business_id)
        if result["status"] == "success":
            logger.info(
                "all_responses route: request completed successfully — "
                f"business='{result.get('business')}', "
                f"total_responses={result['summary']['total_responses']}, "
                f"google_maps={result['summary']['google_maps']}, "
                f"yelp={result['summary']['yelp']}, "
                f"tripadvisor={result['summary']['tripadvisor']}"
            )
        else:
            logger.warning(
                f"all_responses route: request completed with error — "
                f"message={result.get('message')}"
            )
        return result
    except Exception as e:
        logger.exception(f"all_responses route: request failed — {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"All responses lookup failed: {str(e)}",
        )
