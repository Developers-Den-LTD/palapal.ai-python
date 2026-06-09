from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from ddgs import DDGS



from schema.url_finder import BusinessSearchRequest
from utils.auth_utils import verify_secret_key  # Import your function here
from services.logger_services import logger

router = APIRouter(tags=["Scraper"], prefix="/scraper")


# Helper functions for validation
def is_valid_yelp(url: str) -> bool:
    return "yelp.com/biz/" in url

def is_valid_tripadvisor(url: str) -> bool:
    return (
        "tripadvisor.com/Restaurant_Review" in url or
        "tripadvisor.com/Hotel_Review" in url or
        "tripadvisor.com/Attraction_Review" in url
    )


@router.post("/find-business", status_code=status.HTTP_200_OK,dependencies=[Depends(verify_secret_key)])
def find_business_links(payload: BusinessSearchRequest):

    logger.info(
        f"find_business_links: request received business='{payload.business_name}', "
        f"location='{payload.location}', exact_place='{payload.exact_place}'"
    )

    # Extract values from the Pydantic payload
    query_components = [payload.business_name]
    if payload.exact_place:
        query_components.append(payload.exact_place)
    query_components.append(payload.location)
    query_components.append("yelp tripadvisor")
    
    search_query = " ".join(query_components)

    logger.info(f"find_business_links: search query='{search_query}'")

    yelp_url = None
    tripadvisor_url = None

    try:
        with DDGS() as ddgs:
            results = ddgs.text(search_query, max_results=40)

            for r in results:
                url = r.get("href", "")

                if yelp_url is None and is_valid_yelp(url):
                    yelp_url = url
                    logger.info(f"find_business_links: found Yelp URL={yelp_url}")

                if tripadvisor_url is None and is_valid_tripadvisor(url):
                    tripadvisor_url = url
                    logger.info(f"find_business_links: found TripAdvisor URL={tripadvisor_url}")

                if yelp_url and tripadvisor_url:
                    break
    except Exception as e:
        logger.exception(f"find_business_links: search service error — {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search service error: {str(e)}"
        )

    if not yelp_url and not tripadvisor_url:
        logger.warning(
            f"find_business_links: no business found for name='{payload.business_name}', "
            f"query='{search_query}'"
        )
        return {
            "status": "not_found",
            "message": f"No business exists with this name: {payload.business_name}",
            "search_query": search_query,
            "results": {
                "yelp": None,
                "tripadvisor": None
            }
        }

    logger.info(
        f"find_business_links: success yelp={yelp_url}, tripadvisor={tripadvisor_url}"
    )

    return {
        "status": "success",
        "search_query": search_query,
        "results": {
            "yelp": yelp_url,
            "tripadvisor": tripadvisor_url
        }
    }

