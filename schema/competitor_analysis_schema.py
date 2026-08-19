from pydantic import BaseModel, Field


class CompetitorAnalysisRequest(BaseModel):
    business_id: str = Field(..., description="UUID of the primary business")
    competitor_ids: list[str] = Field(
        ...,
        description="List of competitor business UUIDs to compare against",
    )
