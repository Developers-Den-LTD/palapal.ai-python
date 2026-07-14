from pydantic import BaseModel, Field


class ActionCardsRequest(BaseModel):
    business_name: str = Field(
        ...,
        description="Business name used when calculating DDI score (matches DDI_score folder and S3 key)",
        min_length=1,
    )
