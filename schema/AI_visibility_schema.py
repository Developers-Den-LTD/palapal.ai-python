from pydantic import BaseModel, Field


class AIVisibilityRequest(BaseModel):
    business_name: str = Field(..., description="Name of the business to check visibility for", min_length=1)
    business_loc: str = Field(..., description="City or region (e.g. Islamabad)", min_length=1)
    business_type: str = Field(..., description="Type of business (e.g. hotel, restaurant)", min_length=1)
