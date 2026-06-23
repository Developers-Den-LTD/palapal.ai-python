from pydantic import BaseModel, Field


class TechnicalFoundationRequest(BaseModel):
    business_name: str = Field(
        ...,
        description="Name of the business (used to load scraped NAP data)",
        min_length=1,
    )
    website_url: str = Field(
        ...,
        description="Website URL to analyze (e.g. https://example.com)",
    )
