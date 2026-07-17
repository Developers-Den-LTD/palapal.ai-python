from fastapi import APIRouter, Depends, HTTPException, status

from schema.url_finder import BusinessSearchRequest
from services.logger_services import logger
from services.url_finder_services import find_business_links as run_find_business_links

router = APIRouter(tags=["Scraper"], prefix="/scraper")


@router.post(
    "/find-business",
    status_code=status.HTTP_200_OK
)
def find_business_links(payload: BusinessSearchRequest):
    try:
        return run_find_business_links(payload)
    except Exception as e:
        logger.exception(f"find_business_links: search service error — {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search service error: {str(e)}",
        )
