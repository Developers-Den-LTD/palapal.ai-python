from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks


from services.logger_services import logger
from schema.scrapper import ScrapeRequest
from services.scrapper_services import scrape_reviews as run_scrape_reviews
from services.webhook_poster import post_to_webhook
from utils.auth_utils import verify_secret_key


router = APIRouter(
    tags=["Multi-Platform Review Scraper API"]
)


async def _run_scrape_reviews_and_notify(payload: ScrapeRequest) -> None:
    try:
        webhook_url = str(payload.webhook_url)
        result = run_scrape_reviews(payload)

        print("sending result to webhook:", result)
        logger.info(f"\n\nscrape_reviews background: sending result to webhook: {result}")
        await post_to_webhook(webhook_url, result)
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to scrape reviews: {str(e)}",
        )


@router.post(
    "/api/scrape-reviews",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_secret_key)]
)

def scrape_reviews(payload: ScrapeRequest, background_tasks: BackgroundTasks):
    try:
        logger.info(
            f"scrape_reviews route: POST /api/scrape-reviews — "
            f"business='{payload.business_name}', "
            f"type='{payload.exact_place}', loc='{payload.location}', "
            f"yelp_url='{payload.yelp_url}', "
            f"tripadvisor_url='{payload.tripadvisor_url}', "
            f"webhook_url='{payload.webhook_url}'"
        )

        if not payload.webhook_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="webhook_url is required",
            )
        

        background_tasks.add_task(_run_scrape_reviews_and_notify, payload)
        return {
            "status": "accepted",
            "message": "Scraping reviews is being processed in the background.",
        }
    
    except (OSError, TypeError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save scrape results: {str(e)}",
        )
