from pydantic import BaseModel, Field


class ReviewVelocityRequest(BaseModel):
    business_name: str = Field(
        ...,
        description="Name of the business whose scraped reviews should be analyzed",
        min_length=1,
    )
