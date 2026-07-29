from typing import Optional, Union

from pydantic import BaseModel, Field, HttpUrl


class ScrapeRequest(BaseModel):
    business_name: str
    business_id: Optional[Union[str, int]] = Field(
        None,
        description="Client-side business identifier echoed back in the webhook payload",
    )
    branch_name: Optional[str] = Field(
        None,
        description="Optional branch label used to narrow the Google Maps text search",
    )
    location: str
    exact_place: Optional[str] = Field(
        None,
        description=(
            "Optional text hint for search (street/landmark). "
            "If this value looks like a Google place ID it is used for place matching instead."
        ),
    )
    google_place_id: Optional[str] = Field(
        None,
        description="Google Maps place ID used to pick the correct branch from Apify results",
    )
    yelp_url: Optional[str] = None
    tripadvisor_url: Optional[str] = None
    webhook_url: HttpUrl = Field(
        ...,
        description="URL that receives the scrape result via POST when processing completes",
    )
