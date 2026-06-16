from pydantic import BaseModel, Field


class TechnicalFoundationRequest(BaseModel):
    website_url: str = Field(
        ...,
        description="Website URL to analyze (e.g. https://example.com)",
    )
