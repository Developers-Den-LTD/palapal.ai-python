from typing import Optional, Union

from pydantic import BaseModel, Field


class PendingResponsesRequest(BaseModel):
    business_name: str = Field(
        ...,
        description="Business name used when scraping (matches scraping_results folder and S3 key)",
        min_length=1,
    )
    business_id: Optional[Union[str, int]] = Field(
        None,
        description=(
            "Client-side business identifier. When provided, scraped data is "
            "resolved using businessname_businessid."
        ),
    )
