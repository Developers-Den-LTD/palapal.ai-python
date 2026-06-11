from fastapi import APIRouter, HTTPException, status

from schema.scrapper import ScrapeRequest
from services.scrapper_services import scrape_reviews as run_scrape_reviews

router = APIRouter(
    tags=["Multi-Platform Review Scraper API"]
)


@router.post("/api/scrape-reviews")
def scrape_reviews(payload: ScrapeRequest):
    try:
        return run_scrape_reviews(payload)
    except (OSError, TypeError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save scrape results: {str(e)}",
        )
