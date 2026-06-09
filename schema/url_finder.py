from typing import Optional
from pydantic import BaseModel, Field


class BusinessSearchRequest(BaseModel):
    business_name: str = Field(..., description="The name of the business (e.g., McDonald's)", min_length=1)
    location: str = Field(..., description="The city or region (e.g., Manchester)", min_length=1)
    exact_place: Optional[str] = Field(None, description="Optional specific branch, street, or landmark")

