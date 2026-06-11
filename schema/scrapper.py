from typing import Optional
from pydantic import BaseModel

class ScrapeRequest(BaseModel):
    business_name: str
    location: str
    exact_place: str
    yelp_url: Optional[str] = None
    tripadvisor_url: Optional[str] = None