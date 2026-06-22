from typing import Optional
from pydantic import BaseModel, Field, HttpUrl

class ScrapeRequest(BaseModel):
    business_name: str
    location: str
    exact_place: str
    yelp_url: Optional[str] = None
    tripadvisor_url: Optional[str] = None
    webhook_url: HttpUrl = Field(...,description="URL that receives the final DDI score result via POST when processing completes",)