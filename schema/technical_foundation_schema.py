from typing import Optional, Union

from pydantic import BaseModel, Field


class TechnicalFoundationRequest(BaseModel):
    business_name: str = Field(
        ...,
        description="Name of the business (used to load scraped NAP data)",
        min_length=1,
    )
    business_id: Optional[Union[str, int]] = Field(
        None,
        description=(
            "Client-side business identifier. When provided, scraped data is "
            "resolved using businessname_businessid."
        ),
    )
    website_url: str = Field(
        ...,
        description="Website URL to analyze (e.g. https://example.com)",
    )
