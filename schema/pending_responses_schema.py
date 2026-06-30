from pydantic import BaseModel, Field


class PendingResponsesRequest(BaseModel):
    business_name: str = Field(
        ...,
        description="Business name used when scraping (matches scraping_results folder and S3 key)",
        min_length=1,
    )
