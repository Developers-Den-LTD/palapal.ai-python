from pydantic import BaseModel, Field, HttpUrl


class DDIScoreRequest(BaseModel):
    business_name: str = Field(
        ...,
        description="Name of the business",
        min_length=1,
    )
    business_type: str = Field(
        ...,
        description="Type of business (e.g. hotel, restaurant)",
        min_length=1,
    )
    business_loc: str = Field(
        ...,
        description="City or region (e.g. Manchester)",
        min_length=1,
    )
    website_url: str = Field(
        ...,
        description="Website URL to analyze (e.g. https://example.com)",
        min_length=1,
    )

    webhook_url: HttpUrl = Field(
        ...,
        description="URL that receives the final DDI score result via POST when processing completes",
    )
