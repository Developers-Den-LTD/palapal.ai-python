from fastapi import APIRouter, HTTPException, status

from schema.url_finder import BusinessSearchRequest, WebsiteCrawlRequest
from services.logger_services import logger
from services.url_finder_services import (
    find_business_links as run_find_business_links,
    run_website_crawl,
)

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


@router.post(
    "/crawl-website",
    status_code=status.HTTP_200_OK,
)
def crawl_website_route(payload: WebsiteCrawlRequest):
    """
    Crawl all internal pages on a website (same domain only).
    Starts at start_url and follows links up to max_pages.
    """
    logger.info(
        f"crawl_website route: POST /scraper/crawl-website — "
        f"start_url='{payload.start_url}', max_pages={payload.max_pages}"
    )
    try:
        return run_website_crawl(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception(f"crawl_website route: request failed — {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Website crawl failed: {str(exc)}",
        )
