from typing import Optional, Union

from pydantic import BaseModel, Field


class ReviewVelocityRequest(BaseModel):
    business_name: str = Field(
        ...,
        description="Name of the business whose scraped reviews should be analyzed",
        min_length=1,
    )
    business_id: Optional[Union[str, int]] = Field(
        None,
        description=(
            "Client-side business identifier. When provided, scraped data is "
            "resolved using businessname_businessid."
        ),
    )
